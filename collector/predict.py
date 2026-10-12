"""Trip planner model: predict how a train will run on a future date.

Works on dashboard leg rows (see build.py):
  [date, train, dir, cvs_min, cvs_delay, x_min, dur_min, x_delay, canceled, est]

For a route (CVS <-> one station, one direction), a train and a travel date, each
past trip of that train on that route gets a weight:

  w = 0.5 ** (age / HALF_LIFE)                                   recency
    + SEASON * exp(-(doy_gap / SEASON_SIGMA)^2 / 2) * 0.5 ** (age / 365)
                                                                 same time of year
  times DOW_WEIGHT if a different weekday of the same kind (weekday/weekend),
  or DOW_WEIGHT**2 if the other kind.

Only trips within WINDOW_DAYS of the travel date count. The prediction is the
weighted on-time share, shrunk toward the whole route's weighted share by
SHRINK pseudo-trips, plus weighted delay percentiles and cancellation share.
The constants came from backtesting ~1,500 CVS<->WAS/NYP trips (Jun-Oct 2026);
`python -m collector backtest` re-runs that check. site/dashboard.html carries
a JavaScript copy of this model, which must stay in step with this file.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Sequence

HALF_LIFE = 30.0
SEASON = 1.0
SEASON_SIGMA = 30.0
DOW_WEIGHT = 0.7
WINDOW_DAYS = 455
SHRINK = 10.0
OPTION_LOOKBACK = 56          # a train is an option if it ran that weekday in the last 8 weeks


def trip_date(leg: Sequence) -> date:
    """Departure date of the trip: the CVS date when leaving CVS, otherwise the
    origin station's date (earlier when the trip crosses midnight)."""
    d = date.fromisoformat(leg[0])
    if leg[2] == 0:
        return d
    return d + timedelta(days=math.floor((leg[3] - leg[6]) / 1440))


def arrival_delay(leg: Sequence) -> int | None:
    return leg[7] if leg[2] == 0 else leg[4]


def dep_min(leg: Sequence) -> int:
    return leg[3] if leg[2] == 0 else leg[5]


def arr_min(leg: Sequence) -> int:
    return leg[5] if leg[2] == 0 else leg[3]


def _doy_gap(a: date, b: date) -> int:
    d = abs(a.timetuple().tm_yday - b.timetuple().tm_yday)
    return min(d, 365 - d)


def weight(when: date, target: date, use_dow: bool = True) -> float:
    age = (target - when).days
    w = 0.5 ** (age / HALF_LIFE)
    if SEASON:
        w += SEASON * math.exp(-0.5 * (_doy_gap(when, target) / SEASON_SIGMA) ** 2) * 0.5 ** (age / 365)
    if use_dow and when.weekday() != target.weekday():
        same_kind = (when.weekday() >= 5) == (target.weekday() >= 5)
        w *= DOW_WEIGHT if same_kind else DOW_WEIGHT ** 2
    return w


def weighted_quantile(pairs: list[tuple[float, float]], q: float) -> float | None:
    """pairs of (value, weight); value where cumulative weight first reaches q."""
    if not pairs:
        return None
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= q * total - 1e-12:
            return v
    return pairs[-1][0]


@dataclass(frozen=True)
class Prediction:
    train: int
    dep_min: int | None       # latest scheduled times seen in history (None if route_only)
    arr_min: int | None
    on_time: float            # probability of arriving within the threshold
    p10: float | None
    p25: float | None
    p50: float | None
    p75: float | None
    p90: float | None
    late30: float | None      # probability of 30+ min late
    canceled: float           # weighted share of trips canceled
    trips: float              # effective number of trips behind the estimate
    route_only: bool = False  # no history for this train: based on the route's other trains


def _usable(legs: Iterable[Sequence], target: date, history_end: date) -> list[tuple[Sequence, date]]:
    lo = target - timedelta(days=WINDOW_DAYS)
    out = []
    for leg in legs:
        d = trip_date(leg)
        if lo <= d <= history_end and d < target:
            out.append((leg, d))
    return out


def predict(route_legs: Sequence[Sequence], train: int, target: date,
            history_end: date | None = None, on_time_min: int = 15) -> Prediction | None:
    """route_legs: all legs for one station and direction. history_end: last date
    of data the prediction may use (defaults to the day before `target`).
    A train with no history on the route (e.g. a track-work number from the
    timetable) is predicted from all of the route's trains, flagged route_only;
    None only when the route itself has no usable history."""
    history_end = history_end or target - timedelta(days=1)
    usable = _usable(route_legs, target, history_end)
    mine = [(l, d) for l, d in usable if l[1] == train]
    route_only = not mine
    if route_only:
        if not usable:
            return None
        mine = usable
    latest = None if route_only else max(mine, key=lambda x: x[1])[0]

    def tally(rows, use_dow):
        sw = sw2 = s_on = s_cancel = s_all = 0.0
        delays = []
        for leg, d in rows:
            w = weight(d, target, use_dow)
            s_all += w
            if leg[8]:
                s_cancel += w
                continue
            a = arrival_delay(leg)
            if a is None:
                continue
            sw += w
            sw2 += w * w
            s_on += w * (a <= on_time_min)
            delays.append((a, w))
        return sw, sw2, s_on, s_cancel, s_all, delays

    sw, sw2, s_on, s_cancel, s_all, delays = tally(mine, True)
    rw, _, r_on, *_ = tally(usable, False)
    p_route = r_on / rw if rw else 0.65
    if sw:
        neff = sw * sw / sw2
        p = (neff * (s_on / sw) + SHRINK * p_route) / (neff + SHRINK)
    else:
        neff, p = 0.0, p_route
    return Prediction(
        train=train, dep_min=dep_min(latest) if latest else None, arr_min=arr_min(latest) if latest else None, on_time=p,
        p10=weighted_quantile(delays, 0.1), p25=weighted_quantile(delays, 0.25), p50=weighted_quantile(delays, 0.5),
        p75=weighted_quantile(delays, 0.75), p90=weighted_quantile(delays, 0.9),
        late30=(sum(w for a, w in delays if a >= 30) / sw) if sw else None,
        canceled=s_cancel / s_all if s_all else 0.0, trips=neff, route_only=route_only,
    )


def options(route_legs: Sequence[Sequence], target: date, history_end: date) -> list[tuple[int, int, int]]:
    """Fallback when the published timetable (schedule.py) doesn't cover `target`.
    Trains expected on `target`: those that ran on the same weekday within the
    last OPTION_LOOKBACK days of history, with their latest times for that weekday.
    Returns (train, dep_min, arr_min) sorted by departure."""
    since = history_end - timedelta(days=OPTION_LOOKBACK)
    latest: dict[int, tuple[date, Sequence]] = {}
    for leg in route_legs:
        d = trip_date(leg)
        if since < d <= history_end and d.weekday() == target.weekday():
            if leg[1] not in latest or d > latest[leg[1]][0]:
                latest[leg[1]] = (d, leg)
    out = [(n, dep_min(leg), arr_min(leg)) for n, (_, leg) in latest.items()]
    return sorted(out, key=lambda t: (t[1], t[0]))


def backtest(route_legs: Sequence[Sequence], test_from: date, horizon_days: int = 7,
             on_time_min: int = 15) -> dict:
    """Predict every trip on or after test_from using only data from at least
    `horizon_days` earlier. Returns Brier scores for the model and for the
    train's plain 1-year average."""
    by_train: dict[int, list] = {}
    model = base = 0.0
    n = 0
    for leg in route_legs:
        d = trip_date(leg)
        a = arrival_delay(leg)
        if d < test_from or leg[8] or a is None:
            continue
        cutoff = d - timedelta(days=horizon_days)
        pred = predict(route_legs, leg[1], d, history_end=cutoff, on_time_min=on_time_min)
        if pred is None or pred.route_only:
            continue
        hist = by_train.setdefault(leg[1], [(trip_date(l), arrival_delay(l)) for l in route_legs
                                            if l[1] == leg[1] and not l[8] and arrival_delay(l) is not None])
        year = [x for t, x in hist if d - timedelta(days=365) <= t <= cutoff]
        p_base = sum(x <= on_time_min for x in year) / len(year) if year else pred.on_time
        y = a <= on_time_min
        model += (pred.on_time - y) ** 2
        base += (p_base - y) ** 2
        n += 1
    return {"trips": n, "model_brier": model / n if n else None, "baseline_brier": base / n if n else None}
