from datetime import date

import pytest

from collector.asm import AsmClient, parse_run, train_url


def test_train_url():
    assert train_url(date(2026, 10, 10), 19) == "https://asm-backend.transitdocs.com/train/2026/10/10/AMTRAK/19"


def test_client_uses_url():
    seen = []
    AsmClient(fetch=lambda u: seen.append(u)).train_run(date(2026, 1, 2), 156)
    assert seen == ["https://asm-backend.transitdocs.com/train/2026/01/02/AMTRAK/156"]


def test_parse_run_fields(crescent, stations):
    rows = parse_run(crescent, stations.tz)
    assert len(rows) == 34
    cvs = next(r for r in rows if r.station == "CVS")
    assert (cvs.train_id, cvs.origin_date, cvs.train_num, cvs.train_name) == ("2026-10-10_AMTK_19", "2026-10-10", 19, "Crescent")
    assert (cvs.origin, cvs.destination, cvs.seq) == ("NYP", "NOL", 11)
    assert cvs.sched_arr == "2026-10-10T20:47:00-04:00" and cvs.sched_dep == "2026-10-10T20:52:00-04:00"


def test_variance_sign_positive_means_early(crescent, stations):
    rows = {r.station: r for r in parse_run(crescent, stations.tz)}
    assert (rows["CVS"].arr_delay, rows["CVS"].dep_delay) == (7, 9)   # variance -420/-540 -> late
    assert rows["WAS"].arr_delay == -4                                  # variance +240 -> 4 min early
    assert rows["CVS"].arr_type == "ACTUAL"


def test_origin_has_no_arrival_and_terminal_uses_local_tz(crescent, stations):
    rows = {r.station: r for r in parse_run(crescent, stations.tz)}
    assert rows["NYP"].sched_arr == "" and rows["NYP"].arr_delay is None
    assert rows["NOL"].sched_arr.endswith("-05:00")  # Central time
    assert rows["NOL"].canceled and rows["NOL"].arr_type == "ESTIMATED"


@pytest.mark.parametrize("raw", [None, [], {}, {"stops": "x"}, {"stops": [], "number": "x", "train_id": "a", "origin_date": "b"}])
def test_parse_run_malformed(raw, stations):
    assert parse_run(raw, stations.tz) == []


def test_parse_run_skips_bad_stops(crescent, stations):
    crescent["stops"] = [{"miles": 1}, "junk", *crescent["stops"][:2]]
    crescent["stops"][2]["arrive"] = {"variance": "?"}
    rows = parse_run(crescent, stations.tz)
    assert [r.station for r in rows] == ["NYP", "NWK"] and rows[0].arr_delay is None
