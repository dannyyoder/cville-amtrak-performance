"""Amtrak's published timetable (GTFS) for trains that stop at CVS.

`python -m collector schedule` downloads https://content.amtrak.com/content/gtfs/GTFS.zip
and writes data/schedule.json, which the trip planner uses to list the trains
running on a future date (the feed covers about a year ahead).

GTFS stop times are in the agency's time zone (America/New_York) and count from
midnight of the trip's service date, so they can exceed 24:00 for trains that
started the day before. Each stop is stored as
  [code, arr, dep, tz_offset, pickup, dropoff]
with arr/dep in agency minutes from service-date midnight, tz_offset the stop's
local offset from Eastern in minutes (e.g. -60 for Central; constant across DST
for every station these trains serve), and pickup/dropoff 1 if allowed.

Amtrak numbers alternate schedules of a train 1000 higher (1151 runs as 151),
so train numbers 1000-1999 are folded back onto the regular number.
"""
from __future__ import annotations

import csv
import io
import json
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .asm import USER_AGENT, FetchError

GTFS_URL = "https://content.amtrak.com/content/gtfs/GTFS.zip"
HOME = "CVS"
AGENCY_TZ = "America/New_York"
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def fetch_gtfs(url: str = GTFS_URL, timeout: float = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except OSError as e:
        raise FetchError(f"{url}: {e}") from e


def _minutes(hms: str) -> int | None:
    if not hms:
        return None
    h, m, *_ = hms.split(":")
    return int(h) * 60 + int(m)


def _gdate(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def train_number(short_name: str) -> int | None:
    try:
        n = int(short_name)
    except ValueError:
        return None
    return n - 1000 if 1000 <= n < 2000 else n


def _tz_offset(tz: str | None, on: date) -> int:
    """Minutes the stop's zone is ahead of Eastern (negative = behind)."""
    if not tz or tz == AGENCY_TZ:
        return 0
    noon = datetime(on.year, on.month, on.day, 12, tzinfo=timezone.utc)
    a = noon.astimezone(ZoneInfo(tz)).utcoffset()
    b = noon.astimezone(ZoneInfo(AGENCY_TZ)).utcoffset()
    return int((a - b).total_seconds() // 60)


def extract(gtfs: bytes | str | Path, home: str = HOME, today: date | None = None) -> dict[str, Any]:
    """Pull the rail trips that stop at `home` out of a GTFS zip."""
    z = zipfile.ZipFile(io.BytesIO(gtfs) if isinstance(gtfs, bytes) else gtfs)

    def rows(name: str):
        if name not in z.namelist():
            return []
        return csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig"))

    today = today or date.today()
    rail = {r["route_id"]: r.get("route_long_name") or r.get("route_short_name") or ""
            for r in rows("routes.txt") if r.get("route_type") == "2"}
    stop_tz = {r["stop_id"]: r.get("stop_timezone") or "" for r in rows("stops.txt")}
    cal = {r["service_id"]: r for r in rows("calendar.txt")}
    trips = {r["trip_id"]: r for r in rows("trips.txt") if r["route_id"] in rail}

    by_trip: dict[str, list[dict]] = {}
    for r in rows("stop_times.txt"):
        if r["trip_id"] in trips:
            by_trip.setdefault(r["trip_id"], []).append(r)

    out = []
    for tid, stops in by_trip.items():
        if not any(s["stop_id"] == home for s in stops):
            continue
        t = trips[tid]
        c = cal.get(t["service_id"])
        n = train_number(t.get("trip_short_name", ""))
        if c is None or n is None:
            continue
        stops.sort(key=lambda s: int(s["stop_sequence"]))
        out.append({
            "train": n,
            "number": t.get("trip_short_name", ""),
            "route": rail[t["route_id"]],
            "headsign": t.get("trip_headsign", ""),
            "start": _gdate(c["start_date"]),
            "end": _gdate(c["end_date"]),
            "days": "".join("1" if c.get(d) == "1" else "0" for d in DAYS),
            "stops": [[s["stop_id"], _minutes(s["arrival_time"]), _minutes(s["departure_time"]),
                       _tz_offset(stop_tz.get(s["stop_id"]), today),
                       0 if s.get("pickup_type") == "1" else 1,
                       0 if s.get("drop_off_type") == "1" else 1] for s in stops],
        })
    out.sort(key=lambda x: (x["train"], x["start"], x["number"]))
    info = next(iter(rows("feed_info.txt")), {})
    return {
        "feed_version": info.get("feed_version", ""),
        "start": min((x["start"] for x in out), default=None),
        "end": max((x["end"] for x in out), default=None),
        "trips": out,
    }


def write(schedule: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schedule, separators=(",", ":")) + "\n")


def runs_on(trip: dict, service_date: date) -> bool:
    d = service_date.isoformat()
    return trip["start"] <= d <= trip["end"] and trip["days"][service_date.weekday()] == "1"


def options(schedule: dict, station: str, direction: int, target: date, home: str = HOME) -> list[dict]:
    """Trains scheduled to carry a passenger from CVS to `station` (direction 0)
    or from `station` to CVS (direction 1), departing on `target` (local date at
    the boarding station). Sorted by departure; each item has train, number,
    dep and arr (local minutes after midnight), dur (scheduled minutes) and
    days_later (arrival day minus departure day)."""
    origin, dest = (home, station) if direction == 0 else (station, home)
    found: dict[tuple[int, int], dict] = {}
    for trip in schedule.get("trips", []):
        idx = {s[0]: i for i, s in enumerate(trip["stops"])}
        if origin not in idx or dest not in idx or idx[origin] >= idx[dest]:
            continue
        o, d = trip["stops"][idx[origin]], trip["stops"][idx[dest]]
        dep, arr = (o[2] if o[2] is not None else o[1]), (d[1] if d[1] is not None else d[2])
        if dep is None or arr is None or not o[4] or not d[5]:
            continue
        dep_local, arr_local = dep + o[3], arr + d[3]
        service = target - timedelta(days=dep_local // 1440)
        if not runs_on(trip, service):
            continue
        key = (trip["train"], dep_local % 1440)
        found[key] = {
            "train": trip["train"], "number": trip["number"],
            "dep": dep_local % 1440, "arr": arr_local % 1440, "dur": arr - dep,
            "days_later": arr_local // 1440 - dep_local // 1440,
        }
    return sorted(found.values(), key=lambda x: (x["dep"], x["train"]))


def covers(schedule: dict | None, target: date) -> bool:
    return bool(schedule and schedule.get("start") and schedule["start"] <= target.isoformat() <= schedule["end"])


def load(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


__all__ = ["GTFS_URL", "covers", "extract", "fetch_gtfs", "load", "options", "runs_on", "train_number", "write"]
