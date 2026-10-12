from dataclasses import replace

from collector import storage
from collector.asm import parse_run


def test_roundtrip_and_upsert_replaces_run(tmp_path, crescent, stations):
    rows = parse_run(crescent, stations.tz)
    assert storage.upsert(tmp_path, rows) == 1
    path = tmp_path / "2026" / "2026-10.csv"
    assert path.exists()
    assert list(storage.iter_all(tmp_path)) == sorted(rows, key=lambda r: r.seq)

    firmer = [replace(r, arr_type="ACTUAL", arr_delay=55) if r.station == "NOL" else r for r in rows]
    other = [replace(r, train_id="2026-10-10_AMTK_20", train_num=20) for r in rows[:3]]
    storage.upsert(tmp_path, firmer + other)
    back = list(storage.iter_all(tmp_path))
    assert len(back) == len(rows) + 3
    nol = next(r for r in back if r.station == "NOL" and r.train_num == 19)
    assert (nol.arr_delay, nol.arr_type, nol.canceled) == (55, "ACTUAL", True)


def test_missing_root_reads_nothing(tmp_path):
    assert list(storage.iter_all(tmp_path / "nope")) == []
