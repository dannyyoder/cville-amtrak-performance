"""Client for the Amtrak Status Maps (transitdocs) backend.

`GET https://asm-backend.transitdocs.com/train/{yyyy}/{mm}/{dd}/AMTRAK/{num}`
returns one run of a train, keyed by its origin date. Each stop has scheduled
times (epoch seconds) and, once known, a `variance` in seconds that is
**scheduled minus actual** (positive = early), confirmed from the ASM web
app's own code (`actual = sched - variance`). Unknown runs return HTTP 404.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

ASM_BASE = "https://asm-backend.transitdocs.com"
USER_AGENT = "cville-amtrak-performance/1.0 (personal on-time stats; github.com/dannyyoder)"


class FetchError(Exception):
    """Network, HTTP (other than 404), or JSON failure."""


def fetch_json(url: str, timeout: float = 20) -> Any | None:
    """GET a JSON URL. Returns None on 404; raises FetchError on anything else."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise FetchError(f"{url}: HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise FetchError(f"{url}: {e}") from e


def train_url(run_date: date, num: int) -> str:
    return f"{ASM_BASE}/train/{run_date:%Y/%m/%d}/AMTRAK/{num}"


@dataclass(frozen=True)
class StopRow:
    train_id: str
    origin_date: str  # YYYY-MM-DD
    train_num: int
    train_name: str
    origin: str
    destination: str
    seq: int
    station: str
    sched_arr: str  # local ISO-8601 with offset, or ""
    sched_dep: str
    arr_delay: int | None  # minutes late (negative = early)
    arr_type: str  # ACTUAL / ESTIMATED / ""
    dep_delay: int | None
    dep_type: str
    canceled: bool


FIELDS = list(StopRow.__dataclass_fields__)


def _local_iso(epoch: Any, tz: ZoneInfo) -> str:
    if not isinstance(epoch, (int, float)):
        return ""
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone(tz).isoformat()


def _event(ev: Any) -> tuple[int | None, str]:
    if not isinstance(ev, dict):
        return None, ""
    var = ev.get("variance")
    kind = str(ev.get("type") or "")
    if not isinstance(var, (int, float)):
        return None, kind
    return round(-var / 60), kind


def parse_run(raw: Any, tz_for: Callable[[str], ZoneInfo]) -> list[StopRow]:
    """Turn one ASM train response into stop rows. Malformed input gives []."""
    if not isinstance(raw, dict) or not isinstance(raw.get("stops"), list):
        return []
    try:
        num = int(raw["number"])
        train_id = str(raw["train_id"])
        origin_date = str(raw["origin_date"])
    except (KeyError, TypeError, ValueError):
        return []
    rows = []
    for seq, s in enumerate(raw["stops"]):
        if not isinstance(s, dict) or not s.get("code"):
            continue
        code = str(s["code"])
        tz = tz_for(code)
        arr_delay, arr_type = _event(s.get("arrive"))
        dep_delay, dep_type = _event(s.get("depart"))
        rows.append(StopRow(
            train_id=train_id,
            origin_date=origin_date,
            train_num=num,
            train_name=str(raw.get("name") or ""),
            origin=str(raw.get("origin") or ""),
            destination=str(raw.get("destination") or ""),
            seq=seq,
            station=code,
            sched_arr=_local_iso(s.get("sched_arrive"), tz),
            sched_dep=_local_iso(s.get("sched_depart"), tz),
            arr_delay=arr_delay,
            arr_type=arr_type,
            dep_delay=dep_delay,
            dep_type=dep_type,
            canceled=bool(s.get("canceled")),
        ))
    return rows


class AsmClient:
    def __init__(self, fetch: Callable[[str], Any] = fetch_json):
        self._fetch = fetch

    def train_run(self, run_date: date, num: int) -> Any | None:
        return self._fetch(train_url(run_date, num))
