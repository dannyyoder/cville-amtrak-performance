# Charlottesville Amtrak Performance

Personal project: collect on-time data for every Amtrak train stopping at Charlottesville (CVS) and show it in a filterable dashboard (also embedded in Home Assistant via a Webpage card).

## Source of truth
- Spec: `docs/spec.md`.

## Layout
- `collector/`: stdlib-only Python. `asm.py` (fetch + parse transitdocs), `storage.py` (monthly CSVs), `build.py` (dashboard JSON), `predict.py` (trip planner model), `schedule.py` (Amtrak GTFS timetable → `data/schedule.json`), `validate.py` (prediction log, scoring, monthly retune → `data/predictions/`, `data/validation.json`, `data/model.json`), `cli.py` (`python -m collector update|discover|backfill|schedule|build|site|backtest|predictions|validate|tune`).
- The dashboard's trip planner is a JavaScript copy of `predict.py` and `schedule.options`; change both together and re-check they agree.
- `data/stops/YYYY/YYYY-MM.csv`: collected data (committed). `data/stations.json`: station names and time zones. Known train numbers are derived from the CSVs.
- `site/dashboard.html`: the dashboard page, written as a body fragment (claude.ai artifact format). `collector site` wraps it into a full `index.html` for Pages.
- `.github/workflows/`: collect (daily; also logs and scores predictions), tune (monthly retune), backfill (manual), tests.

## Rules
- Python 3.11+ stdlib only at runtime; pytest for tests. Datetimes are timezone-aware; scheduled times are stored in each station's local time.
- transitdocs `variance` is scheduled − actual (positive = early). Store delay as minutes late = −variance/60. Only `ACTUAL` times count in stats.
- Be polite to transitdocs: sequential requests, 0.5 s pause, no parallel backfills.
- Anything needing the HA UI or GitHub repo settings: ask Danny.

## Commands
- `pip install -r requirements-dev.txt`
- `pytest -q`
- `python -m collector site --out _site && python -m http.server -d _site`
