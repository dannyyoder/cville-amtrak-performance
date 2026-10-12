"""Station metadata (name, city, state, time zone), cached in data/stations.json."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

AMTRAKER_STATIONS = "https://api-v3.amtraker.com/v3/stations"
DEFAULT_TZ = "America/New_York"


def slim(raw: Any) -> dict[str, dict[str, str]]:
    """Reduce Amtraker's /v3/stations payload to {code: {name, city, state, tz}}."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for code, s in raw.items():
        if isinstance(s, dict) and s.get("tz"):
            out[code] = {k: str(s.get(k) or "") for k in ("name", "city", "state", "tz")}
    return dict(sorted(out.items()))


class Stations:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict[str, str]] = {}
        if path.exists():
            self.data = json.loads(path.read_text())

    def refresh(self, fetch: Callable[[str], Any]) -> None:
        fresh = slim(fetch(AMTRAKER_STATIONS))
        if fresh:
            self.data = {**self.data, **fresh}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True) + "\n")

    def tz(self, code: str) -> ZoneInfo:
        return ZoneInfo(self.data.get(code, {}).get("tz") or DEFAULT_TZ)

    def name(self, code: str) -> str:
        return self.data.get(code, {}).get("name") or code
