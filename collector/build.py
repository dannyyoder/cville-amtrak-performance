"""Build the dashboard's JSON from stored stop rows.

Output (in `out/`):
- `index.json`: stations with leg counts, train names, date range, build time.
- `legs/{CODE}.json`: every scheduled trip between CVS and station CODE.
- `summary.json`: compact recent stats for Home Assistant (WAS and NYP).

A leg row is a list:
  [date, train, dir, cvs_min, cvs_delay, x_min, dur_min, x_delay, canceled]
  date       CVS-local date of the scheduled CVS event (YYYY-MM-DD)
  dir        0 = from CVS (depart CVS, arrive X); 1 = to CVS (depart X, arrive CVS)
  cvs_min    scheduled CVS event, minutes after local midnight
  cvs_delay  minutes late at CVS (departure if dir 0, arrival if dir 1), or null
  x_min      scheduled event at X, minutes after X-local midnight
  dur_min    scheduled minutes between the two events
  x_delay    minutes late at X (arrival if dir 0, departure if dir 1), or null
  canceled   1 if either stop was canceled
Delays are only filled from ACTUAL times; ESTIMATED ones are left null.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .asm import StopRow

HOME = "CVS"
ON_TIME_MIN = 15  # Amtrak's customer on-time standard
SUMMARY_STATIONS = ("WAS", "NYP")
SUMMARY_DAYS = 30


def _sched(r: StopRow, prefer: str) -> datetime | None:
    a, d = r.sched_arr, r.sched_dep
    s = (d or a) if prefer == "dep" else (a or d)
    return datetime.fromisoformat(s) if s else None


def _actual(delay: int | None, kind: str) -> int | None:
    return delay if kind == "ACTUAL" else None


def _minute(t: datetime) -> int:
    return t.hour * 60 + t.minute


def legs_for_run(stops: list[StopRow]) -> dict[str, list]:
    """Map station code -> leg row for one run. Empty if the run skips CVS."""
    stops = sorted(stops, key=lambda r: r.seq)
    home = [i for i, r in enumerate(stops) if r.station == HOME]
    if not home:
        return {}
    i = home[0]
    cvs = stops[i]
    out = {}
    for j, x in enumerate(stops):
        if j == i or x.station == HOME:
            continue
        if j > i:  # CVS -> X
            t0, t1 = _sched(cvs, "dep"), _sched(x, "arr")
            cvs_delay, x_delay = _actual(cvs.dep_delay, cvs.dep_type), _actual(x.arr_delay, x.arr_type)
            direction = 0
        else:  # X -> CVS
            t0, t1 = _sched(x, "dep"), _sched(cvs, "arr")
            cvs_delay, x_delay = _actual(cvs.arr_delay, cvs.arr_type), _actual(x.dep_delay, x.dep_type)
            direction = 1
        if t0 is None or t1 is None:
            continue
        cvs_t, x_t = (t0, t1) if direction == 0 else (t1, t0)
        out[x.station] = [
            cvs_t.date().isoformat(), cvs.train_num, direction,
            _minute(cvs_t), cvs_delay, _minute(x_t),
            round((t1 - t0).total_seconds() / 60), x_delay,
            1 if (cvs.canceled or x.canceled) else 0,
        ]
    return out


def _group_runs(rows: Iterable[StopRow]) -> dict[str, list[StopRow]]:
    runs: dict[str, list[StopRow]] = defaultdict(list)
    for r in rows:
        runs[r.train_id].append(r)
    return runs


def _arrival_delay(leg: list) -> int | None:
    return leg[7] if leg[2] == 0 else leg[4]


def summarize(legs: dict[str, list[list]], today: date, names: dict[int, str]) -> dict:
    since = (today - timedelta(days=SUMMARY_DAYS)).isoformat()
    out = {"days": SUMMARY_DAYS, "on_time_minutes": ON_TIME_MIN, "routes": {}}
    for code in SUMMARY_STATIONS:
        for direction, label in ((0, f"{HOME}_to_{code}"), (1, f"{code}_to_{HOME}")):
            rows = [l for l in legs.get(code, []) if l[2] == direction and l[0] >= since and not l[8]]
            by_train: dict[int, list[int]] = defaultdict(list)
            for l in rows:
                d = _arrival_delay(l)
                if d is not None:
                    by_train[l[1]].append(d)
            every = [d for ds in by_train.values() for d in ds]
            out["routes"][label] = {
                **_stats(every),
                "trains": {str(n): {"name": names.get(n, ""), **_stats(ds)} for n, ds in sorted(by_train.items())},
            }
    return out


def _stats(delays: list[int]) -> dict:
    if not delays:
        return {"trips": 0, "on_time_pct": None, "median_delay": None, "avg_delay": None}
    return {
        "trips": len(delays),
        "on_time_pct": round(100 * sum(d <= ON_TIME_MIN for d in delays) / len(delays)),
        "median_delay": round(statistics.median(delays)),
        "avg_delay": round(statistics.fmean(delays), 1),
    }


def build(rows: Iterable[StopRow], out: Path, station_names: dict[str, dict[str, str]],
          now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    legs: dict[str, list[list]] = defaultdict(list)
    names: dict[int, str] = {}
    for stops in _group_runs(rows).values():
        names[stops[0].train_num] = stops[0].train_name
        for code, leg in legs_for_run(stops).items():
            legs[code].append(leg)
    (out / "legs").mkdir(parents=True, exist_ok=True)
    for old in (out / "legs").glob("*.json"):
        old.unlink()
    stations = []
    dates = []
    for code, rows_ in sorted(legs.items()):
        rows_.sort(key=lambda l: (l[0], l[3], l[1]))
        (out / "legs" / f"{code}.json").write_text(json.dumps(rows_, separators=(",", ":")))
        meta = station_names.get(code, {})
        stations.append({"code": code, "name": meta.get("name") or code, "city": meta.get("city", ""),
                         "state": meta.get("state", ""), "legs": len(rows_)})
        dates += [rows_[0][0], rows_[-1][0]]
    index = {
        "generated_at": now.isoformat(timespec="seconds"),
        "home": HOME,
        "first_date": min(dates) if dates else None,
        "last_date": max(dates) if dates else None,
        "trains": {str(n): names[n] for n in sorted(names)},
        "stations": stations,
    }
    (out / "index.json").write_text(json.dumps(index, indent=1))
    summary = summarize(legs, now.date(), names)
    summary["generated_at"] = index["generated_at"]
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    return index
