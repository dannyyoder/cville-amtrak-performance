# Charlottesville Amtrak Performance — Design Spec

**Date:** 2026-10-12
**Status:** Implemented (v1).

## 1. Purpose

Collect on-time data for every Amtrak train that stops at Charlottesville (CVS), and summarize it in a dashboard. The most-used pairs are CVS ↔ Washington (WAS) and CVS ↔ New York (NYP), but the dashboard answers "how late does this trip usually run?" for any CVS ↔ station pair. You can filter by:

- individual train
- weekday or weekend
- day of week
- departure time of day
- date range

The dashboard also has to be viewable inside Home Assistant.

## 2. Decisions

| Topic | Decision |
|---|---|
| Data source | Amtrak Status Maps backend (`asm-backend.transitdocs.com`), which keeps history back to mid-2017. Amtraker v3 is used only for station metadata and spotting live trains. |
| Collector | Python 3.11+ stdlib, run by GitHub Actions twice a day (about 7 AM and 7 PM Eastern). Each run re-fetches the last 4 origin dates, so long-distance trains finish and estimates firm up. |
| Storage | CSV in the repo: `data/stops/YYYY/YYYY-MM.csv`, one row per stop per run, for every run that stops at CVS. |
| History | One-time backfill through the `Backfill history` workflow (manual run) or `python -m collector backfill`. |
| Dashboard | One static page (`site/dashboard.html`) that loads JSON built from the CSVs. Served by GitHub Pages; also publishable as a claude.ai artifact. |
| Home Assistant | A Webpage card pointing at the GitHub Pages URL. `summary.json` gives 30-day stats for WAS and NYP for any future HA sensors. |
| Politeness | One request at a time, 0.5 s pause between requests, and a User-Agent naming the project. A normal update makes about 40 requests. |

## 3. Data source details

`GET https://asm-backend.transitdocs.com/train/{yyyy}/{mm}/{dd}/AMTRAK/{num}` returns one run, keyed by origin date, or HTTP 404 if there's no such run.

- **Run fields:** `train_id` (`2026-10-10_AMTK_19`), `origin_date`, `number`, `name`, `origin`, `destination`, and `stops[]`.
- **Stop fields:** `code`, `sched_arrive` and `sched_depart` (epoch seconds), `arrive` and `depart` (each holds `variance` and `type`), and `canceled`.
- **`variance` is scheduled − actual, in seconds, so a positive value means early.** This comes from the ASM web app's own code (`actual = sched − variance`), checked against Amtraker on train 19 of 2026-10-11 (Trenton +360 = 6 min early).
- **`type`** is `ACTUAL` or `ESTIMATED`. Only `ACTUAL` times feed the delay stats.
- **Which trains:** the train numbers that serve CVS change over the years. As of October 2026 they are 19/20 (Crescent), 50/51 (Cardinal), and Northeast Regionals 66, 147, 151, 156, 171 and 178. They're found three ways:
  - by probing train numbers 1–199 on a Wednesday and a Sunday of each backfilled quarter;
  - by probing every Monday in the daily update;
  - from Amtraker's live train list on every update.

## 4. Dashboard

### Data it loads

The build step writes these files from the CSVs:

- **`index.json`:** stations, train names, and the date range.
- **`legs/{CODE}.json`:** one row per scheduled trip between CVS and that station: `[date, train, dir, cvs_min, cvs_delay, x_min, dur_min, x_delay, canceled]`. Full details are in `collector/build.py`.

### Filters

All in one row; they apply to everything on the page:

- direction (from CVS / to CVS)
- other station
- date range (30 d, 90 d, 12 mo, 3 y, all)
- delay measured at arrival or departure
- on-time threshold (5, 10, 15 or 30 min; 15 is Amtrak's customer standard)
- days (all, weekdays, weekends, or individual days)
- departure time of day (before 9 AM, 9 AM–3 PM, 3–7 PM, after 7 PM)
- trains

### Views

- **KPI row:** on-time %, median and average delay, 90th-percentile delay, typical actual trip time vs. scheduled, and canceled trips.
- **Charts:** delay distribution, on-time % by month, by day of week, and by departure hour.
- **By-train table** (sortable; it also serves as the table view of the charts): scheduled departure and arrival, typical actual arrival, trips, on-time %, median, 90th percentile, worst delay, and cancellations.

### Definitions

- Day of week and date are the CVS-local date of the scheduled CVS stop.
- Time of day is the scheduled departure at the trip's origin station.

## 5. Error handling

| Situation | Behavior |
|---|---|
| A fetch fails (network, 5xx, bad JSON) | Log a warning, skip that run, and retry on the next scheduled update (the 4-day window). |
| 404 | The train didn't run that day; skip it silently. |
| Malformed stop | Skip that stop. A malformed run gives no rows. |
| Run doesn't stop at CVS | Not stored. |
| Data firms up later (ESTIMATED → ACTUAL) | Upsert replaces all rows for that `train_id`. |
| Backfill interrupted | `--max-minutes` stops it cleanly and logs a resume date. Runs already stored are skipped on rerun. |

## 6. Out of scope

Real-time alerts (the old project, now retired), weather or incident attribution, and stations other than CVS as the home station.
