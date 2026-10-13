from datetime import date, timedelta

import pytest

from collector import predict as P
from collector import validate as V


def leg(d, train=20, delay=0, direction=0, canceled=0):
    if direction == 0:
        return [d.isoformat(), train, 0, 659, 0, 1074, 415, delay, canceled, 0]
    return [d.isoformat(), train, 1, 1247, delay, 1110, 137, 0, canceled, 0]


START = date(2026, 1, 5)


@pytest.fixture(autouse=True)
def default_params():
    saved = P.params()
    yield
    P.configure(saved)


def history(days=200, late_every=4, train=20, direction=0):
    return [leg(START + timedelta(days=i), train=train, delay=(40 if i % late_every == 0 else 0), direction=direction)
            for i in range(days)]


def test_baseline_is_plain_share_over_past_year():
    legs = history(100)                        # every 4th trip 40 min late -> 75% on time
    b = V.baseline(legs, 20, START + timedelta(days=120), START + timedelta(days=99))
    assert b == pytest.approx(0.75)


def test_predictions_for_uses_history_options_and_skips_trains_without_history():
    legs = history(120)
    made = START + timedelta(days=119)
    rows = V.predictions_for(legs, None, "WAS", 0, made, made, "v0", leads=(1, 7))
    assert [(r["lead_days"], r["train"]) for r in rows] == [(1, 20), (7, 20)]
    assert 0 < rows[0]["on_time"] < 1 and rows[0]["base_on_time"] == pytest.approx(0.75, abs=0.01)


def test_log_dedupes(tmp_path):
    rows = [{"made_on": "2026-10-12", "travel_date": "2026-10-13", "lead_days": 1, "station": "WAS", "dir": 0, "train": 20,
             "on_time": 0.8, "base_on_time": 0.7, "p10": -5, "p50": 0, "p90": 20, "model": "v0"}]
    assert V.append_log(tmp_path, rows) == 1
    assert V.append_log(tmp_path, rows) == 0                       # same day re-run adds nothing
    assert V.append_log(tmp_path, [{**rows[0], "made_on": "2026-10-13", "lead_days": 0}]) == 1
    back = V.read_log(tmp_path)
    assert len(back) == 2 and back[0]["train"] == "20" and (tmp_path / "2026.csv").exists()


def test_score_joins_outcomes_and_reports_calibration():
    legs = {("WAS", 0): history(60)}
    actual = V.outcomes(legs)
    rows = []
    for i in range(40):
        d = START + timedelta(days=i)
        rows.append({"made_on": (d - timedelta(days=7)).isoformat(), "travel_date": d.isoformat(), "lead_days": 7,
                     "station": "WAS", "dir": 0, "train": 20, "on_time": 0.75, "base_on_time": 0.5,
                     "p10": -5, "p50": 0, "p90": 30, "model": "v0"})
    rows.append({**rows[0], "travel_date": "2030-01-01"})          # hasn't happened: ignored
    s = V.score(rows, actual)
    assert s["trips"] == 40 and s["actual"] == 0.75 and s["predicted"] == 0.75
    assert s["brier"] == pytest.approx(0.1875) and s["baseline_brier"] == pytest.approx(0.25)
    assert s["range_hit"] == 0.75                                  # 40-min delays fall outside -5..30
    assert s["calibration"] == [{"bin": "70-80%", "trips": 40, "predicted": 0.75, "actual": 0.75}]
    assert set(s["by_lead"]) == {7} and set(s["by_route"]) == {"CVS->WAS"}
    assert V.score([], actual) == {"trips": 0}


def test_replay_only_uses_data_from_before_each_prediction():
    legs = {("WAS", 1): history(150, direction=1)}
    rows = V.replay(legs, START + timedelta(days=120), START + timedelta(days=149), lead=7)
    assert len(rows) == 30 and all(r["lead_days"] == 7 and r["dir"] == 1 for r in rows)
    assert all(r["made_on"] < r["travel_date"] for r in rows)


def test_tune_adopts_only_a_better_challenger(monkeypatch):
    # Recent trips flip from always late to always on time: a shorter half-life should win.
    old = [leg(START + timedelta(days=i), delay=40) for i in range(0, 150)]
    new = [leg(START + timedelta(days=i), delay=0) for i in range(150, 300)]
    legs = {("WAS", 0): old + new}
    monkeypatch.setattr(V, "SEARCH", {"HALF_LIFE": (5.0, 90.0)})
    P.configure({"HALF_LIFE": 90.0})
    today = START + timedelta(days=300)
    model, d = V.tune(legs, today, None, log=lambda m: None)
    assert d["adopted"] and model["version"] == 1 and model["params"]["HALF_LIFE"] == 5.0
    assert P.params()["HALF_LIFE"] == 5.0 and len(model["history"]) == 1
    # Running again: nothing better to find, settings and version stay
    model2, d2 = V.tune(legs, today, model, log=lambda m: None)
    assert not d2["adopted"] and model2["version"] == 1 and len(model2["history"]) == 2


def test_load_params_applies_model_json(tmp_path):
    (tmp_path / "model.json").write_text('{"version": 3, "params": {"HALF_LIFE": 45, "NOT_A_PARAM": 1}}')
    m = P.load_params(tmp_path / "model.json")
    assert m["version"] == 3 and P.params()["HALF_LIFE"] == 45.0
    assert P.load_params(tmp_path / "missing.json") is None
