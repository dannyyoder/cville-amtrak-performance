"""Command line: `python -m collector {update,discover,backfill,schedule,build,site,backtest}`."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from . import predict, schedule, storage
from .asm import AsmClient, FetchError, StopRow, fetch_json, parse_run
from .build import HOME, _group_runs, build, legs_for_run
from .stations import Stations

log = logging.getLogger("collector")
EASTERN = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent
PROBE_RANGE = range(1, 200)  # every train number that has served CVS is below 200
AMTRAKER_TRAINS = "https://api-v3.amtraker.com/v3/trains"


class Collector:
    def __init__(self, data_dir: Path, client: AsmClient | None = None,
                 fetch: Callable[[str], Any] = fetch_json, delay: float = 0.5,
                 sleep: Callable[[float], None] = time.sleep):
        self.data_dir = data_dir
        self.stops_dir = data_dir / "stops"
        self.stations = Stations(data_dir / "stations.json")
        self.client = client or AsmClient(fetch)
        self.fetch = fetch
        self.delay = delay
        self.sleep = sleep
        self.failures = 0
        self.flush_rows = 5000
        # Known train numbers and stored runs come from the CSVs themselves, so
        # concurrent collector and backfill runs never conflict on a shared index.
        self.last_seen: dict[int, str] = {}
        self.stored_ids: set[str] = set()
        for r in storage.iter_all(self.stops_dir):
            self._note(r)

    # --- fetching -------------------------------------------------------
    def fetch_run(self, run_date: date, num: int) -> list[StopRow] | None:
        """Rows for one run if it stops at CVS; [] if it exists but skips CVS; None if absent/failed."""
        try:
            raw = self.client.train_run(run_date, num)
        except FetchError as e:
            log.warning("fetch failed: %s", e)
            self.failures += 1
            return None
        finally:
            self.sleep(self.delay)
        if raw is None:
            return None
        rows = parse_run(raw, self.stations.tz)
        return rows if any(r.station == HOME for r in rows) else []

    def collect(self, dates: Iterable[date], nums: Iterable[int], skip_stored: bool = False) -> int:
        nums = sorted(set(nums))
        stored = self.stored_ids if skip_stored else set()
        found: list[StopRow] = []
        saved = 0
        for d in dates:
            for n in nums:
                if f"{d.isoformat()}_AMTK_{n}" in stored:
                    continue
                rows = self.fetch_run(d, n)
                if rows:
                    found += rows
            if len(found) > self.flush_rows:  # checkpoint long backfills
                saved += self._save(found)
                found = []
        return saved + self._save(found)

    def discover(self, dates: Iterable[date]) -> set[int]:
        """Probe every candidate train number on `dates`; return those that stop at CVS."""
        hits = set()
        for d in dates:
            for n in PROBE_RANGE:
                rows = self.fetch_run(d, n)
                if rows:
                    hits.add(n)
                    self._save(rows)
        log.info("discovered %s on %s", sorted(hits), [d.isoformat() for d in dates])
        return hits

    def live_numbers(self) -> set[int]:
        """Train numbers Amtraker currently shows with a CVS stop (cheap, one request)."""
        try:
            raw = self.fetch(AMTRAKER_TRAINS)
        except FetchError as e:
            log.warning("amtraker live list failed: %s", e)
            return set()
        nums = set()
        for runs in (raw or {}).values() if isinstance(raw, dict) else ():
            for t in runs if isinstance(runs, list) else ():
                if not isinstance(t, dict) or t.get("provider") not in (None, "Amtrak"):
                    continue
                if any(isinstance(s, dict) and s.get("code") == HOME for s in t.get("stations") or ()):
                    try:
                        nums.add(int(t["trainNum"]))
                    except (KeyError, TypeError, ValueError):
                        pass
        return nums

    # --- bookkeeping ----------------------------------------------------
    def _note(self, r: StopRow) -> None:
        self.stored_ids.add(r.train_id)
        if r.origin_date > self.last_seen.get(r.train_num, ""):
            self.last_seen[r.train_num] = r.origin_date

    def _save(self, rows: list[StopRow]) -> int:
        if not rows:
            return 0
        n = storage.upsert(self.stops_dir, rows)
        for r in rows:
            self._note(r)
        return n

    def known_numbers(self, active_since: date | None = None) -> set[int]:
        return {n for n, last in self.last_seen.items() if active_since is None or last >= active_since.isoformat()}


def daterange(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def quarter_probe_dates(start: date) -> list[date]:
    """A Wednesday and a Sunday in the second week of `start`'s window."""
    base = start + timedelta(days=7)
    wed = base + timedelta(days=(2 - base.weekday()) % 7)
    sun = base + timedelta(days=(6 - base.weekday()) % 7)
    return [wed, sun]


def cmd_update(c: Collector, args) -> None:
    today = datetime.now(EASTERN).date()
    if not c.stations.data:
        c.stations.refresh(c.fetch)
    nums = c.known_numbers(active_since=today - timedelta(days=60)) | c.live_numbers()
    if args.probe:
        nums |= c.discover([today - timedelta(days=2)])
    dates = daterange(today - timedelta(days=args.days), today - timedelta(days=1))
    n = c.collect(dates, nums)
    log.info("update: %d runs stored for %s..%s, trains %s", n, dates[0], dates[-1], sorted(nums))


def cmd_discover(c: Collector, args) -> None:
    dates = [date.fromisoformat(d) for d in args.dates]
    print(sorted(c.discover(dates)))


def cmd_backfill(c: Collector, args) -> None:
    if not c.stations.data:
        c.stations.refresh(c.fetch)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    deadline = time.monotonic() + args.max_minutes * 60 if args.max_minutes else None
    w = start
    while w <= end:
        w_end = min(end, (w.replace(day=1) + timedelta(days=95)).replace(day=1) - timedelta(days=1))
        nums = c.known_numbers()
        if not args.no_discover:
            nums |= c.discover([d for d in quarter_probe_dates(w) if d <= w_end])
        n = c.collect(daterange(w, w_end), nums, skip_stored=True)
        log.info("backfill %s..%s: %d runs, trains %s", w, w_end, n, sorted(nums))
        w = w_end + timedelta(days=1)
        if deadline and time.monotonic() > deadline:
            log.info("stopping at time limit; resume with --start %s", w)
            break


def cmd_schedule(c: Collector, args) -> None:
    """Download Amtrak's GTFS timetable and save the trains through CVS."""
    s = schedule.extract(schedule.fetch_gtfs(args.url))
    if not s["trips"]:
        raise SystemExit("GTFS feed had no rail trips through CVS; keeping the previous schedule")
    schedule.write(s, c.data_dir / "schedule.json")
    log.info("schedule: %d trips through CVS, %s..%s (feed %s)", len(s["trips"]), s["start"], s["end"], s["feed_version"])


def _copy_schedule(c: Collector, out: Path) -> None:
    src = c.data_dir / "schedule.json"
    if src.exists():
        (out / "schedule.json").write_text(src.read_text())


def cmd_build(c: Collector, args) -> None:
    idx = build(storage.iter_all(c.stops_dir), Path(args.out), c.stations.data)
    _copy_schedule(c, Path(args.out))
    log.info("built %d stations, %s..%s", len(idx["stations"]), idx["first_date"], idx["last_date"])


PAGE_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>:root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>
</head>
<body>
"""


def cmd_site(c: Collector, args) -> None:
    """Write a deployable static site: index.html (the dashboard page) plus data/."""
    out = Path(args.out)
    build(storage.iter_all(c.stops_dir), out / "data", c.stations.data)
    _copy_schedule(c, out / "data")
    page = (ROOT / "site" / "dashboard.html").read_text()
    (out / "index.html").write_text(PAGE_HEAD + page + "\n</body>\n</html>\n")
    log.info("site written to %s", out)


def cmd_backtest(c: Collector, args) -> None:
    """Check the trip planner model: predict recent arrivals using only older data."""
    legs: dict[tuple[str, int], list] = {}
    for stops in _group_runs(storage.iter_all(c.stops_dir)).values():
        for code, leg in legs_for_run(stops).items():
            if code in args.stations:
                legs.setdefault((code, leg[2]), []).append(leg)
    last = max(predict.trip_date(l) for ls in legs.values() for l in ls)
    test_from = last - timedelta(days=args.days)
    total = model = base = 0.0
    for (code, direction), ls in sorted(legs.items()):
        r = predict.backtest(ls, test_from, horizon_days=args.horizon)
        if not r["trips"]:
            continue
        route = f"CVS->{code}" if direction == 0 else f"{code}->CVS"
        print(f"{route:<9} trips {r['trips']:>5}  model Brier {r['model_brier']:.4f}  train 1-yr average {r['baseline_brier']:.4f}")
        total += r["trips"]
        model += r["model_brier"] * r["trips"]
        base += r["baseline_brier"] * r["trips"]
    if total:
        print(f"{'all':<9} trips {int(total):>5}  model Brier {model / total:.4f}  train 1-yr average {base / total:.4f}"
              f"   (test {test_from}..{last}, predicting {args.horizon} days ahead; lower is better)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="collector")
    p.add_argument("--data", default=str(ROOT / "data"))
    p.add_argument("--delay", type=float, default=0.5, help="seconds between ASM requests")
    sub = p.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update", help="fetch the last few days of runs")
    u.add_argument("--days", type=int, default=4)
    u.add_argument("--probe", action="store_true", help="also probe all train numbers for new CVS trains")
    d = sub.add_parser("discover", help="probe train numbers 1-199 on given dates")
    d.add_argument("dates", nargs="+")
    b = sub.add_parser("backfill", help="fetch history, a quarter at a time")
    b.add_argument("--start", required=True)
    b.add_argument("--end", required=True)
    b.add_argument("--no-discover", action="store_true")
    b.add_argument("--max-minutes", type=float, default=0)
    s = sub.add_parser("build", help="write dashboard JSON")
    s.add_argument("--out", default=str(ROOT / "site" / "data"))
    w = sub.add_parser("site", help="write the full static site (page + data) for GitHub Pages")
    w.add_argument("--out", default=str(ROOT / "_site"))
    g = sub.add_parser("schedule", help="download Amtrak's GTFS timetable for trains through CVS")
    g.add_argument("--url", default=schedule.GTFS_URL)
    t = sub.add_parser("backtest", help="score the trip planner model on recent trips")
    t.add_argument("--days", type=int, default=120, help="test on the last N days")
    t.add_argument("--horizon", type=int, default=7, help="predict this many days ahead")
    t.add_argument("--stations", nargs="+", default=["WAS", "NYP"])
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    c = Collector(Path(args.data), delay=args.delay)
    {"update": cmd_update, "discover": cmd_discover, "backfill": cmd_backfill, "build": cmd_build, "site": cmd_site,
     "schedule": cmd_schedule, "backtest": cmd_backtest}[args.cmd](c, args)
    if c.failures:
        log.warning("%d fetches failed", c.failures)
    return 0


if __name__ == "__main__":
    sys.exit(main())
