"""Validation loop for the trip planner: log, score, and retune.

1. Log (`python -m collector predictions`, every collect run): for each main
   route and lead time in LEADS, record the planner's prediction for every train
   scheduled on today + lead, with a simple baseline (that train's plain on-time
   share over the past year). Rows go to data/predictions/YYYY.csv; a prediction
   is logged once per (made_on, travel date, route, train).
2. Score (`python -m collector validate`, every collect run): join logged
   predictions with what actually happened and summarise accuracy (Brier score
   vs the baseline, calibration, by lead time, route and month) into
   data/validation.json, shown on the dashboard. The same scoring runs on a
   replay of recent history (predictions made 7 days ahead from the data as it
   stood then), so the scorecard has substance before live logs mature.
3. Retune (`python -m collector tune`, monthly): coordinate search over the
   model's weights, choosing on trips 31-120 days old and confirming on the last
   30 days. A challenger replaces the current settings in data/model.json only
   if it is better on both. Every decision is recorded in the model's history.

"On time" here is arrival within 15 minutes, the threshold the logs are scored at.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from . import predict, schedule

ROUTES = (("WAS", 0), ("WAS", 1), ("NYP", 0), ("NYP", 1))
LEADS = (1, 3, 7, 14, 28)
ON_TIME = 15
FIELDS = ["made_on", "travel_date", "lead_days", "station", "dir", "train",
          "on_time", "base_on_time", "p10", "p50", "p90", "model"]
Key = tuple[str, int, int, str]          # (station, dir, train, travel date)


# ---- helpers ----------------------------------------------------------------
def baseline(route_legs: Sequence[Sequence], train: int, target: date, history_end: date) -> float | None:
    """The train's plain on-time share over the 365 days before the prediction."""
    lo = history_end - timedelta(days=365)
    hits = [predict.arrival_delay(l) <= ON_TIME for l, d in predict._dated(route_legs)
            if l[1] == train and lo <= d <= min(history_end, target - timedelta(days=1))
            and not l[8] and predict.arrival_delay(l) is not None]
    return sum(hits) / len(hits) if hits else None


def outcomes(legs_by_route: dict[tuple[str, int], list]) -> dict[Key, int]:
    """Arrival delay (minutes late) of every trip that ran and reported a time."""
    out = {}
    for (station, direction), legs in legs_by_route.items():
        for l, d in predict._dated(legs):
            a = predict.arrival_delay(l)
            if not l[8] and a is not None:
                out[(station, direction, l[1], d.isoformat())] = a
    return out


def _row(made_on: date, target: date, station: str, direction: int, train: int,
         p: predict.Prediction, base: float | None, model: str) -> dict:
    return {
        "made_on": made_on.isoformat(), "travel_date": target.isoformat(),
        "lead_days": (target - made_on).days, "station": station, "dir": direction, "train": train,
        "on_time": round(p.on_time, 4), "base_on_time": "" if base is None else round(base, 4),
        "p10": p.p10, "p50": p.p50, "p90": p.p90, "model": model,
    }


def model_tag(model: dict | None) -> str:
    return f"v{model['version']}" if model else "v0"


# ---- 1. log -----------------------------------------------------------------
def predictions_for(legs: Sequence[Sequence], sched: dict | None, station: str, direction: int,
                    made_on: date, history_end: date, model: str, leads: Iterable[int] = LEADS) -> list[dict]:
    rows = []
    for lead in leads:
        target = made_on + timedelta(days=lead)
        if schedule.covers(sched, target):
            trains = [o["train"] for o in schedule.options(sched, station, direction, target)]
        else:
            trains = [t[0] for t in predict.options(legs, target, history_end)]
        for n in trains:
            p = predict.predict(legs, n, target, history_end=history_end, on_time_min=ON_TIME)
            if p is None or p.route_only:
                continue
            rows.append(_row(made_on, target, station, direction, n, p, baseline(legs, n, target, history_end), model))
    return rows


def _log_path(root: Path, made_on: str) -> Path:
    return root / f"{made_on[:4]}.csv"


def read_log(root: Path) -> list[dict]:
    rows = []
    for path in sorted(root.glob("*.csv")):
        with path.open(newline="") as f:
            rows.extend(csv.DictReader(f))
    return rows


def append_log(root: Path, rows: list[dict]) -> int:
    """Add rows not already logged (same made_on, travel date, route, train). Returns how many were added."""
    root.mkdir(parents=True, exist_ok=True)
    seen = {(r["made_on"], r["travel_date"], r["station"], str(r["dir"]), str(r["train"])) for r in read_log(root)}
    new = [r for r in rows if (r["made_on"], r["travel_date"], r["station"], str(r["dir"]), str(r["train"])) not in seen]
    by_file = defaultdict(list)
    for r in new:
        by_file[_log_path(root, r["made_on"])].append(r)
    for path, rs in by_file.items():
        fresh = not path.exists()
        with path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
            if fresh:
                w.writeheader()
            w.writerows(rs)
    return len(new)


# ---- replay -----------------------------------------------------------------
def replay(legs_by_route: dict[tuple[str, int], list], start: date, end: date, lead: int = 7,
           model: str = "replay") -> list[dict]:
    """What the planner would have said `lead` days ahead for each trip that ran in [start, end]."""
    rows = []
    for (station, direction), legs in legs_by_route.items():
        for l, d in predict._dated(legs):
            if not (start <= d <= end) or l[8] or predict.arrival_delay(l) is None:
                continue
            made_on = d - timedelta(days=lead)
            p = predict.predict(legs, l[1], d, history_end=made_on, on_time_min=ON_TIME)
            if p is None or p.route_only:
                continue
            rows.append(_row(made_on, d, station, direction, l[1], p, baseline(legs, l[1], d, made_on), model))
    return rows


# ---- 2. score ---------------------------------------------------------------
def _f(v) -> float | None:
    return None if v in ("", None) else float(v)


def score(rows: Iterable[dict], actual: dict[Key, int]) -> dict:
    """Accuracy of predictions whose trips have happened."""
    joined = []
    for r in rows:
        a = actual.get((r["station"], int(r["dir"]), int(r["train"]), r["travel_date"]))
        if a is not None:
            joined.append((r, a <= ON_TIME, a))
    if not joined:
        return {"trips": 0}

    def block(items):
        n = len(items)
        if not n:
            return {"trips": 0}
        model = sum((_f(r["on_time"]) - y) ** 2 for r, y, _ in items) / n
        based = [(_f(r["base_on_time"]), y) for r, y, _ in items if _f(r["base_on_time"]) is not None]
        within = [_f(r["p10"]) <= a <= _f(r["p90"]) for r, _, a in items if _f(r["p10"]) is not None]
        return {
            "trips": n,
            "brier": round(model, 4),
            "baseline_brier": round(sum((p - y) ** 2 for p, y in based) / len(based), 4) if based else None,
            "predicted": round(sum(_f(r["on_time"]) for r, _, _ in items) / n, 3),
            "actual": round(sum(y for _, y, _ in items) / n, 3),
            "range_hit": round(sum(within) / len(within), 3) if within else None,   # share inside the 8-in-10 range
        }

    calib = []
    for lo in range(10):
        sel = [(r, y, a) for r, y, a in joined if lo / 10 <= _f(r["on_time"]) < (lo + 1) / 10 or (lo == 9 and _f(r["on_time"]) == 1)]
        if sel:
            calib.append({"bin": f"{lo * 10}-{lo * 10 + 10}%", "trips": len(sel),
                          "predicted": round(sum(_f(r["on_time"]) for r, _, _ in sel) / len(sel), 3),
                          "actual": round(sum(y for _, y, _ in sel) / len(sel), 3)})
    group = lambda key: {k: block(v) for k, v in sorted(_groups(joined, key).items())}
    return {
        **block(joined),
        "calibration": calib,
        "by_lead": group(lambda r: int(r["lead_days"])),
        "by_route": group(lambda r: (f"CVS->{r['station']}" if int(r["dir"]) == 0 else f"{r['station']}->CVS")),
        "by_month": group(lambda r: r["travel_date"][:7]),
        "first": min(r["travel_date"] for r, _, _ in joined),
        "last": max(r["travel_date"] for r, _, _ in joined),
    }


def _groups(joined, key):
    g = defaultdict(list)
    for item in joined:
        g[key(item[0])].append(item)
    return g


# ---- 3. tune ----------------------------------------------------------------
SEARCH = {
    "HALF_LIFE": (15.0, 20.0, 30.0, 45.0, 60.0, 90.0),
    "SEASON": (0.0, 0.5, 1.0, 2.0),
    "SEASON_SIGMA": (15.0, 30.0, 45.0),
    "DOW_WEIGHT": (0.5, 0.7, 0.85, 1.0),
    "SHRINK": (3.0, 10.0, 20.0),
}
MIN_GAIN = 0.0005     # a challenger must beat the current settings by at least this Brier margin


def _brier_with(values: dict, legs_by_route, start: date, end: date) -> tuple[float, int]:
    saved = predict.params()
    predict.configure(values)
    try:
        rows = replay(legs_by_route, start, end)
    finally:
        predict.configure(saved)
    actual = outcomes(legs_by_route)
    s = score(rows, actual)
    return s.get("brier", 1.0), s.get("trips", 0)


def tune(legs_by_route, today: date, current: dict | None, log=print) -> tuple[dict, dict]:
    """Coordinate search on trips 31-120 days old, confirmed on the last 30 days.
    Returns (model to save, decision record)."""
    select = (today - timedelta(days=120), today - timedelta(days=31))
    confirm = (today - timedelta(days=30), today - timedelta(days=1))
    champion = predict.params()
    champ_sel, n_sel = _brier_with(champion, legs_by_route, *select)
    best, best_sel = dict(champion), champ_sel
    for _ in range(2):                                    # two passes over the weights
        for name, choices in SEARCH.items():
            for v in choices:
                if v == best[name]:
                    continue
                trial = {**best, name: v}
                b, _ = _brier_with(trial, legs_by_route, *select)
                log(f"  {name}={v:g}: {b:.4f}")
                if b < best_sel:
                    best, best_sel = trial, b
    champ_conf, n_conf = _brier_with(champion, legs_by_route, *confirm)
    chal_conf, _ = _brier_with(best, legs_by_route, *confirm)
    adopt = best != champion and best_sel <= champ_sel - MIN_GAIN and chal_conf <= champ_conf
    decision = {
        "date": today.isoformat(), "adopted": adopt,
        "current": champion, "challenger": best,
        "select": {"from": select[0].isoformat(), "to": select[1].isoformat(), "trips": n_sel,
                   "current_brier": round(champ_sel, 4), "challenger_brier": round(best_sel, 4)},
        "confirm": {"from": confirm[0].isoformat(), "to": confirm[1].isoformat(), "trips": n_conf,
                    "current_brier": round(champ_conf, 4), "challenger_brier": round(chal_conf, 4)},
    }
    model = dict(current or {"version": 0, "params": champion, "history": []})
    model["history"] = list(model.get("history", [])) + [decision]
    model["last_checked"] = today.isoformat()
    if adopt:
        model["version"] = int(model.get("version", 0)) + 1
        model["params"] = best
        model["updated"] = today.isoformat()
        predict.configure(best)
    return model, decision


def save_model(model: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model, indent=1) + "\n")
