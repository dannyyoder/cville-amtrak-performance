from datetime import date, timedelta

import pytest

from collector import predict as P


def leg(d, train=20, delay=0, direction=0, canceled=0, dep=659, arr=1074, dur=415):
    """A leg row; for direction 1 the CVS event is the arrival."""
    if direction == 0:
        return [d.isoformat(), train, 0, dep, 0, arr, dur, delay, canceled, 0]
    return [d.isoformat(), train, 1, arr, delay, dep, dur, 0, canceled, 0]


TUE = date(2026, 10, 6)   # a Tuesday


def test_trip_date_for_overnight_trip_into_cvs():
    # arrives CVS 6:00 AM after a 10-hour trip: departed the previous day
    assert P.trip_date(leg(TUE, direction=1, arr=360, dep=1200, dur=600)) == TUE - timedelta(days=1)
    assert P.trip_date(leg(TUE, direction=1, arr=1247, dep=1110, dur=137)) == TUE
    assert P.trip_date(leg(TUE)) == TUE


def test_recency_halves_every_half_life(monkeypatch):
    monkeypatch.setattr(P, "SEASON", 0.0)          # isolate the recency term
    t = TUE + timedelta(days=200)
    near, far = P.weight(t - timedelta(days=7), t), P.weight(t - timedelta(days=7 + 28), t)   # same weekday
    assert far == pytest.approx(near * 0.5 ** (28 / P.HALF_LIFE))


def test_same_season_last_year_beats_off_season_of_similar_age():
    t = date(2026, 7, 15)
    same_season = P.weight(date(2025, 7, 15), t)       # 365 days old, same week
    off_season = P.weight(date(2025, 7, 15) + timedelta(days=0), date(2026, 1, 15))
    assert same_season > 5 * off_season


def test_day_of_week_weights(monkeypatch):
    monkeypatch.setattr(P, "SEASON", 0.0)
    t = TUE + timedelta(days=28)                   # a Tuesday
    same = P.weight(TUE, t)
    wed = P.weight(TUE + timedelta(days=1), t)     # one day newer, so undo the recency gain
    sat = P.weight(TUE + timedelta(days=4), t)
    gain = lambda days: 0.5 ** (-days / P.HALF_LIFE)
    assert wed / (same * gain(1)) == pytest.approx(P.DOW_WEIGHT)
    assert sat / (same * gain(4)) == pytest.approx(P.DOW_WEIGHT ** 2)


def test_weighted_quantile():
    assert P.weighted_quantile([(10, 1), (0, 1), (30, 2)], 0.5) == 10
    assert P.weighted_quantile([(10, 1), (0, 1), (30, 2)], 0.9) == 30
    assert P.weighted_quantile([], 0.5) is None


def test_recent_form_outweighs_old_form():
    target = TUE + timedelta(days=70)
    old_bad = [leg(TUE - timedelta(days=7 * i), delay=45) for i in range(10, 30)]       # months ago: late
    recent_good = [leg(TUE + timedelta(days=7 * i), delay=2) for i in range(0, 9)]      # recent weeks: on time
    p = P.predict(old_bad + recent_good, 20, target)
    assert p.on_time > 0.6 and p.p50 == 2


def test_shrinks_toward_route_when_train_has_little_history():
    target = TUE + timedelta(days=14)
    others = [leg(TUE - timedelta(days=i), train=66, delay=0) for i in range(60)]       # route mostly on time
    lone = [leg(TUE, train=50, delay=90)]                                               # one awful trip
    p = P.predict(others + lone, 50, target)
    assert 0.5 < p.on_time < 1.0 and p.trips == pytest.approx(1.0)
    assert P.predict(others, 50, target) is None


def test_cancellations_and_missing_delays():
    target = TUE + timedelta(days=7)
    rows = [leg(TUE, canceled=1), leg(TUE - timedelta(days=7), delay=None), leg(TUE - timedelta(days=14), delay=5)]
    p = P.predict(rows, 20, target)
    assert 0 < p.canceled < 1 and p.p50 == 5 and p.late30 == 0


def test_history_end_is_respected():
    target = TUE + timedelta(days=30)
    rows = [leg(TUE - timedelta(days=7 * i), delay=0) for i in range(8)] + [leg(TUE + timedelta(days=21), delay=99)]
    assert P.predict(rows, 20, target, history_end=TUE).p50 == 0


def test_options_use_latest_times_for_that_weekday():
    end = date(2026, 10, 11)                          # Sunday
    rows = [
        leg(date(2026, 10, 6), train=20, arr=1122),   # Tuesday schedule
        leg(date(2026, 10, 4), train=20, arr=1074),   # Sunday schedule
        leg(date(2026, 9, 27), train=20, arr=1068),   # older Sunday schedule
        leg(date(2026, 10, 9), train=50, dep=944),    # Friday-only train
        leg(date(2026, 6, 2), train=134, dep=535),    # Tuesday, but too long ago
    ]
    assert P.options(rows, date(2026, 10, 13), end) == [(20, 659, 1122)]           # a Tuesday
    assert P.options(rows, date(2026, 10, 18), end) == [(20, 659, 1074)]           # a Sunday
    assert [t[0] for t in P.options(rows, date(2026, 10, 16), end)] == [50]        # a Friday


def test_backtest_runs():
    rows = [leg(TUE - timedelta(days=i), delay=(30 if i % 3 == 0 else 0)) for i in range(200)]
    out = P.backtest(rows, TUE - timedelta(days=30))
    assert out["trips"] > 20 and 0 <= out["model_brier"] <= 1 and 0 <= out["baseline_brier"] <= 1
