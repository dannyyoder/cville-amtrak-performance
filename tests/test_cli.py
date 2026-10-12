import json
from datetime import date

from collector.asm import FetchError
from collector.cli import Collector, quarter_probe_dates


class FakeClient:
    def __init__(self, runs):
        self.runs, self.calls = runs, []

    def train_run(self, d, n):
        self.calls.append((d, n))
        v = self.runs.get((d, n))
        if isinstance(v, Exception):
            raise v
        return v


def make(tmp_path, stations, runs):
    (tmp_path / "stations.json").write_text(json.dumps(stations.data))
    return Collector(tmp_path, client=FakeClient(runs), delay=0, sleep=lambda s: None)


def test_collect_keeps_only_cvs_runs_and_tracks_trains(tmp_path, stations, crescent):
    no_cvs = {**crescent, "train_id": "2026-10-10_AMTK_67", "number": 67,
              "stops": [s for s in crescent["stops"] if s["code"] != "CVS"]}
    d = date(2026, 10, 10)
    c = make(tmp_path, stations, {(d, 19): crescent, (d, 67): no_cvs, (d, 5): FetchError("boom")})
    assert c.collect([d], [19, 67, 5, 99]) == 1
    assert c.failures == 1
    assert c.last_seen == {19: "2026-10-10"}
    assert (tmp_path / "stops" / "2026" / "2026-10.csv").exists()
    # a fresh collector rebuilds what it knows from the stored CSVs
    again = make(tmp_path, stations, {})
    assert again.known_numbers(active_since=date(2026, 10, 1)) == {19}
    assert again.known_numbers(active_since=date(2026, 10, 11)) == set()
    assert "2026-10-10_AMTK_19" in again.stored_ids


def test_collect_skip_stored(tmp_path, stations, crescent):
    d = date(2026, 10, 10)
    c = make(tmp_path, stations, {(d, 19): crescent})
    c.collect([d], [19])
    c.client.calls.clear()
    c.collect([d], [19], skip_stored=True)
    assert c.client.calls == []


def test_discover_probes_range(tmp_path, stations, crescent):
    d = date(2026, 10, 10)
    c = make(tmp_path, stations, {(d, 19): crescent})
    assert c.discover([d]) == {19}
    assert len(c.client.calls) == 199


def test_live_numbers(tmp_path, stations):
    payload = {"19": [{"trainNum": "19", "provider": "Amtrak", "stations": [{"code": "CVS"}]}],
               "84": [{"trainNum": "84", "provider": "Amtrak", "stations": [{"code": "WAS"}]}],
               "x": "junk"}
    c = make(tmp_path, stations, {})
    c.fetch = lambda url: payload
    assert c.live_numbers() == {19}


def test_quarter_probe_dates():
    wed, sun = quarter_probe_dates(date(2026, 1, 1))
    assert wed.weekday() == 2 and sun.weekday() == 6 and date(2026, 1, 8) <= min(wed, sun)


def test_collect_counts_runs_across_checkpoints(tmp_path, stations, crescent):
    days = [date(2026, 10, d) for d in (1, 2, 3)]
    runs = {(d, 19): {**crescent, "train_id": f"{d}_AMTK_19", "origin_date": d.isoformat()} for d in days}
    c = make(tmp_path, stations, runs)
    c.flush_rows = 40  # one run is 34 rows, so this checkpoints after day 2
    assert c.collect(days, [19]) == 3
    assert len(c.stored_ids) == 3
