# Charlottesville Amtrak Performance

Tracks how late Amtrak trains run at Charlottesville (CVS). A GitHub Actions job collects reported arrival and departure times twice a day for every train that stops at CVS. The times come from [Amtrak Status Maps](https://asm.transitdocs.com) and are stored as CSV under `data/`. A static dashboard summarizes delays between CVS and any other station on those trains, such as Washington, New York, Lynchburg, or Atlanta. You can filter by train, weekday or weekend, day of week, departure time, and date range.

## Running it

```sh
pip install -r requirements-dev.txt && pytest       # tests (runtime is stdlib only)
python -m collector update                          # fetch the last 4 days
python -m collector backfill --start 2024-01-01 --end 2024-12-31
python -m collector site --out _site                # build the dashboard + data
python -m http.server -d _site                      # view at http://localhost:8000
```

## Workflows

- **Collect and publish** runs at about 7 AM and 7 PM Eastern. It fetches recent runs, commits `data/`, and redeploys the dashboard to GitHub Pages.
- **Backfill history** is run by hand with a start and end date. Run it about one year at a time; each run stops after roughly 5 hours and logs where to resume.
- **Tests** runs pytest.

## Dashboard on GitHub Pages and in Home Assistant

The repo is public and Pages is set to deploy from GitHub Actions (Settings → Pages → Source), so every run of "Collect and publish" redeploys the dashboard at `https://dannyyoder.github.io/cville-amtrak-performance/`.

In Home Assistant, edit a dashboard and add a **Webpage** card with that URL. The page works at phone width, and it follows the light/dark setting of the device or browser viewing it.

`data/summary.json` in the built site has 30-day stats for CVS ↔ WAS and CVS ↔ NYP, ready for future HA sensors.

## Docs

- Spec: [`docs/spec.md`](docs/spec.md)
