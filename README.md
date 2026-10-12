# CVS Train Alerts

An AppDaemon app for Home Assistant that polls the unofficial Amtraker v3 API and pops up a Windows toast (via HASS.Agent) on a home PC whenever an Amtrak train arrives at the station picked on the Home Assistant "Train Alerts" dashboard (Charlottesville, CVS, by default). Each alert shows the route name, train number, destination, and how many minutes early or late the train arrived. The dashboard also controls which devices get alerts, dry-run mode, and a test button.

## Running the tests

```sh
pip install -r requirements-dev.txt && pytest
```

## Docs

- Design spec (source of truth): [`docs/superpowers/specs/2026-10-03-cvs-train-alerts-design.md`](docs/superpowers/specs/2026-10-03-cvs-train-alerts-design.md)
- Implementation plan: [`docs/superpowers/plans/2026-10-03-cvs-train-alerts.md`](docs/superpowers/plans/2026-10-03-cvs-train-alerts.md)
