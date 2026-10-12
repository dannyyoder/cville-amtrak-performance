# CVS Train Alerts

Personal, for-fun project: a Windows toast pops up on a home PC whenever an Amtrak train arrives at a station chosen in Home Assistant (default Charlottesville, CVS). Toast shows route name, train number, destination, and exact minutes early/late.

## Source of truth
- Spec: `docs/superpowers/specs/2026-10-03-cvs-train-alerts-design.md` (wins over the plan where they differ)
- Plan: `docs/superpowers/plans/2026-10-03-cvs-train-alerts.md`

## Stack
- AppDaemon app on Home Assistant OS 2026.9.1.
- Data: Amtraker v3 API, `https://api-v3.amtraker.com/v3` (unofficial, no key). Poll every 90 s, 10 s timeout, sequential requests.
- Delivery: HASS.Agent 2.x on a Windows 11 PC → HA notify service. Mosquitto broker + MQTT integration. HASS.Agent HA integration via HACS custom repo.
- Controls: HA helpers + a storage-mode "Train Alerts" dashboard (station dropdown, per-device toggles, dry run, test button, last-arrival sensor).

## Rules
- Python 3.11+ stdlib only at runtime; pytest for tests. All datetimes timezone-aware.
- Only `app.py` imports AppDaemon; everything else is pure and unit-tested.
- Never send to `notify.notify`. Missing/unavailable toggle = off. Missing/unavailable dry-run helper = dry run ON.
- HA calls add-ons "Apps" now. Configure HA through the UI or an MCP connector, never by hand-editing `configuration.yaml`.
- Anything that needs the HA UI, the HA box's filesystem, or a home PC: stop and ask Danny.

## Commands
- `pip install -r requirements-dev.txt`
- `pytest -v`
