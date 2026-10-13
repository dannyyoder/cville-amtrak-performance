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
| Collector | Python 3.11+ stdlib, run by GitHub Actions once a day (about 7 AM Eastern). Each run re-fetches the last 4 origin dates, so long-distance trains finish and estimates firm up. |
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
- date range (30 d, 90 d, 12 mo, 3 y, all, or a custom range picked on one calendar: click a start day, then an end day)
- delay measured at arrival or departure
- on-time threshold (5, 10, 15 or 30 min; 15 is Amtrak's customer standard)
- days (all, weekdays, weekends, or individual days)
- departure time of day (before 9 AM, 9 AM–3 PM, 3–7 PM, after 7 PM)
- trains

Every filter, including direction and station, has its own **Reset** button next to its label (greyed out while it's already at its default). **Reset all filters**, at the top of the filter panel with a count of active filters, returns the page to its default view: CVS → Washington, last 90 days, arrival delay, 15-minute threshold, all days, times and trains, and the box-plot chart style.

### Views

- **KPI row:** on-time %, median and average delay, 90th-percentile delay, typical actual trip time vs. scheduled, and canceled trips.
- **Delay by train:** one row per train with a curve of its minutes late (smoothed with a 5-minute kernel; taller = more trips arrived then) and a tick under it for every trip, on one scale shared by all trains (at least 30 minutes early to at most 120 late; longer delays are counted as "+N later"). An orange line marks the scheduled time and a ring the median. Trains with fewer than 15 trips in the filtered data show ticks only. Follows every filter, including arrival/departure.
- **Charts:** delay distribution, on-time % by month, by day of week, and by departure hour.
- **By-train table** (sortable; it also serves as the table view of the charts): scheduled departure and arrival, typical actual arrival, trips, on-time %, median, 90th percentile, worst delay, and cancellations.

### Definitions

- Day of week and date are the CVS-local date of the scheduled CVS stop.
- Time of day is the scheduled departure at the trip's origin station.

### Trip planner

The page has two tabs: **Past performance** (the filters, charts and table above; shown by default) and **Plan a trip**. Opening the page with `#plan` in the address goes straight to the planner.

"Plan a trip" has its own controls: a travel date (today through one year ahead, picked on a calendar), a direction, and the other station. It lists every train expected that day with:

- the expected travel time (scheduled departure to likely arrival, e.g. "2 hours and 43 minutes", with the scheduled time when they differ),

- the chance of arriving on time (within the dashboard's "On time means within" setting; arrival at the destination),
- the likely arrival time, and the range 8 in 10 trips fall within,
- the chance of arriving 30+ minutes late, and of a cancellation when that isn't negligible,
- a "why" line: that train's plain on-time rate over the last 8 weeks and over the same weeks a year earlier,
- a "Best odds" mark on the train with the highest on-time chance,
- a small arrival curve: the weighted distribution of minutes late, smoothed with a 6-minute kernel (taller = more likely), with a vertical line labeled with the scheduled arrival time and a ring at the most likely arrival. All of that day's trains share one scale (from at least 30 minutes early to 90 minutes late; longer tails are noted "→ later").

A collapsible "How the prediction works" section on the tab explains the weights below in plain language, with example weights.

**Which trains:** from Amtrak's published GTFS timetable (`https://content.amtrak.com/content/gtfs/GTFS.zip`, about a year ahead), refreshed by the collect workflow each run. `python -m collector schedule` (`collector/schedule.py`) keeps only rail trips through CVS and saves them to `data/schedule.json`: each trip's train number, service dates and weekdays, and every stop's times. GTFS times are Eastern and count from the trip's service date, so they can pass 24:00; each stop also stores its offset from Eastern, and pickup/drop-off rules are respected. Alternate-schedule numbers 1000–1999 are folded onto the regular train (1151 runs as 151). A trip is offered for a travel date when the service date implied by its departure time from the boarding station runs that day. Thruway buses are excluded.

For dates past the end of the timetable, the planner falls back to history: a train is listed if it ran on the same day of the week within the last 8 weeks, at its latest times. A timetable train with no history on the route (for example a track-work number) is predicted from the route's other trains and labelled so.

**Model** (`collector/predict.py`, with a JavaScript copy in the dashboard that must stay identical). Each past trip of the train on the route, from the 15 months before the travel date, gets a weight:

- recency: halves every 30 days;
- time of year: a bonus for trips within about a month of the same date in another year (Gaussian on day-of-year distance, σ = 30 days), itself fading with a one-year half-life, so a trip a year ago in the same season still counts;
- day of week: ×0.7 for a different weekday of the same kind (weekday/weekend), ×0.49 for the other kind.

The on-time chance is the weighted share of trips on time, shrunk toward the whole route's weighted share by 10 pseudo-trips (so a train with little history leans on the others). Delay percentiles and the 30+ minute share come from the same weights; cancellation share counts canceled trips with the same weights. For travel dates far ahead, recency weights fade and the same season last year dominates.

**Accuracy:** `python -m collector backtest` predicts every CVS ↔ WAS/NYP arrival in the last 120 days using only data from at least 7 days earlier. On 2026-06-14..2026-10-12 (1,486 trips) the Brier score was 0.188, against 0.201 for each train's plain one-year average and 0.245 for a single overall rate; it beat the plain average on all four routes, and predicted probabilities matched observed on-time rates closely. The starting constants were chosen from that backtest.

**Factors the model uses:** the specific train and route, recency, time of year, day of week, time of day (through the train's scheduled time), and the route's other trains when a train has little history. Weather was tested (NOAA daily observations at ~33 stations along the routes; 95°F+ heat and 40 mph gusts cut on-time odds noticeably beyond the seasonal effect) but is not used in the planner; the page's "How the prediction works" section says so.

### Validation loop

`collector/validate.py`; results in `data/validation.json` and the planner's "How accurate is it?" section.

1. **Log** (`python -m collector predictions`, each collect run): for CVS↔WAS and CVS↔NYP in both directions, the planner's prediction for every train scheduled 1, 3, 7, 14 and 28 days ahead, plus a baseline (that train's plain on-time share over the past year), the 8-in-10 range, and the model version. Rows go to `data/predictions/YYYY.csv`, once per prediction date, travel date, route and train.
2. **Score** (`python -m collector validate`, each collect run): once trips run, predictions are joined with actual arrivals (on time = within 15 minutes) and summarised: Brier score against the baseline, mean predicted vs actual, share of arrivals inside the 8-in-10 range, calibration by probability band, and breakdowns by lead time, route and month. The same scoring runs on a replay of the last 180 days (predictions made 7 days ahead from the data available then), so there is a report before live logs mature.
3. **Retune** (`python -m collector tune`, monthly workflow "Retune trip planner"): a coordinate search over the half-life, season bonus and spread, day-of-week factor and shrinkage, choosing on trips 31–120 days old and confirming on the last 30 days. New settings are adopted only if they beat the current ones on both (by at least 0.0005 on the first), and saved in `data/model.json`, which the Python model and the dashboard both read; every decision is appended to the file's history.

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
