import json
from dataclasses import replace
from datetime import date, datetime, timezone

from collector.asm import parse_run
from collector.build import build, legs_for_run, summarize


def test_legs_into_cvs(crescent, stations):
    legs = legs_for_run(parse_run(crescent, stations.tz))
    # depart WAS 18:30 (on time) -> arrive CVS 20:47, 7 min late
    assert legs["WAS"] == ["2026-10-10", 19, 1, 20 * 60 + 47, 7, 18 * 60 + 30, 137, 0, 0]
    assert legs["NYP"][:7] == ["2026-10-10", 19, 1, 1247, 7, 14 * 60, 407]


def test_legs_out_of_cvs_and_estimates_are_null(crescent, stations):
    legs = legs_for_run(parse_run(crescent, stations.tz))
    lyh = legs["LYH"]
    assert lyh[2] == 0 and lyh[3] == 20 * 60 + 52 and lyh[4] == 9  # CVS departure 20:52, 9 late
    nol = legs["NOL"]
    assert nol[7] is None and nol[8] == 1  # ESTIMATED arrival not used; canceled flagged
    assert (nol[5], nol[6]) == (21 * 60 + 12, 1520)  # 21:12 Central the next day


def test_run_without_cvs_has_no_legs(crescent, stations):
    rows = [r for r in parse_run(crescent, stations.tz) if r.station != "CVS"]
    assert legs_for_run(rows) == {}


def test_summary_on_time_and_window():
    legs = {"WAS": [
        ["2026-10-01", 66, 0, 600, 0, 750, 150, 3, 0],
        ["2026-10-02", 66, 0, 600, 0, 750, 150, 40, 0],
        ["2026-10-03", 66, 0, 600, 0, 750, 150, None, 0],   # no actual
        ["2026-10-04", 66, 0, 600, 0, 750, 150, 0, 1],      # canceled
        ["2026-08-01", 66, 0, 600, 0, 750, 150, 99, 0],     # outside 30 days
        ["2026-10-05", 19, 1, 1247, 12, 1110, 137, 0, 0],   # into CVS: arrival delay is cvs_delay
    ]}
    s = summarize(legs, date(2026, 10, 12), {66: "Northeast Regional", 19: "Crescent"})
    out = s["routes"]["CVS_to_WAS"]
    assert (out["trips"], out["on_time_pct"], out["median_delay"]) == (2, 50, 22)
    assert out["trains"]["66"]["name"] == "Northeast Regional"
    assert s["routes"]["WAS_to_CVS"]["trains"]["19"]["avg_delay"] == 12.0
    assert s["routes"]["NYP_to_CVS"]["trips"] == 0


def test_build_writes_files(tmp_path, crescent, stations):
    rows = parse_run(crescent, stations.tz)
    rows += [replace(r, train_id="2026-10-11_AMTK_19", origin_date="2026-10-11") for r in rows]
    idx = build(rows, tmp_path, stations.data, now=datetime(2026, 10, 12, tzinfo=timezone.utc))
    assert idx["trains"] == {"19": "Crescent"} and idx["home"] == "CVS"
    was = next(s for s in idx["stations"] if s["code"] == "WAS")
    assert was["name"] == "Washington Union" and was["legs"] == 2
    assert len(json.loads((tmp_path / "legs" / "WAS.json").read_text())) == 2
    assert json.loads((tmp_path / "summary.json").read_text())["routes"]["WAS_to_CVS"]["trips"] == 2
