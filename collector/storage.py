"""Monthly CSV storage of stop rows: data/stops/YYYY/YYYY-MM.csv (by origin date).

Rows for a run (train_id) are replaced wholesale on upsert, so re-fetching a
run as its data firms up from ESTIMATED to ACTUAL is idempotent.
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator

from .asm import FIELDS, StopRow


def month_path(root: Path, origin_date: str) -> Path:
    return root / origin_date[:4] / f"{origin_date[:7]}.csv"


def _to_row(d: dict[str, str]) -> StopRow:
    def opt_int(v: str) -> int | None:
        return int(v) if v not in ("", None) else None

    return StopRow(
        train_id=d["train_id"], origin_date=d["origin_date"], train_num=int(d["train_num"]),
        train_name=d["train_name"], origin=d["origin"], destination=d["destination"],
        seq=int(d["seq"]), station=d["station"], sched_arr=d["sched_arr"], sched_dep=d["sched_dep"],
        arr_delay=opt_int(d["arr_delay"]), arr_type=d["arr_type"],
        dep_delay=opt_int(d["dep_delay"]), dep_type=d["dep_type"],
        canceled=d["canceled"] == "1",
    )


def _to_dict(r: StopRow) -> dict[str, str]:
    d = {f: getattr(r, f) for f in FIELDS}
    d["canceled"] = "1" if r.canceled else "0"
    return {k: "" if v is None else str(v) for k, v in d.items()}


def read_file(path: Path) -> list[StopRow]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [_to_row(d) for d in csv.DictReader(f)]


def write_file(path: Path, rows: Iterable[StopRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r.origin_date, r.train_num, r.train_id, r.seq))
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(_to_dict(r) for r in rows)
    os.replace(tmp, path)


def upsert(root: Path, rows: Iterable[StopRow]) -> int:
    """Replace stored rows for each run in `rows`. Returns the number of runs written."""
    by_file: dict[Path, dict[str, list[StopRow]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_file[month_path(root, r.origin_date)][r.train_id].append(r)
    n = 0
    for path, runs in by_file.items():
        kept = [r for r in read_file(path) if r.train_id not in runs]
        write_file(path, kept + [r for rs in runs.values() for r in rs])
        n += len(runs)
    return n


def iter_all(root: Path) -> Iterator[StopRow]:
    for path in sorted(root.glob("*/*.csv")):
        yield from read_file(path)
