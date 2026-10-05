# Allo Bank™ Competitive Intelligence Monitor

A polished Google Play competitive-intelligence dashboard for Allo Bank™ and 24 selected Indonesian banking / fintech apps.

## Dashboard views

- **Overall** — compare all apps with Category and App filters.
- **Allo Bank™** — benchmark Allo Bank™ against the digital-bank peer set.

## Data package

The dashboard uses the newly supplied 5 Oct 2026 Google Play web extracts in `data/`:

- `app_metrics_latest_web_2026-10-05.csv`
- `reviews_surfaced_web_2026-10-05.csv`
- `method_and_limits_2026-10-05.csv`

Run:

```bash
python build_static_data.py
```

This generates `data/dashboard_data.json`, the browser-ready layer used by the frontend.

## Front-end experience

The UI is designed as an operational monitoring dashboard: live-style status treatment, local clock, manual refresh control, periodic refresh polling, responsive filters, benchmark charts, customer-voice signals, and an Allo Bank focus view.

The interface does not expose implementation wording such as "static dataset". The data remains traceable through the supplied CSV files and methodology document in `data/`.

## Local run

Double-click `open_dashboard.bat`, or run:

```bash
python -m http.server 8000
```

then open `http://localhost:8000`.

## GitHub Pages

The workflow in `.github/workflows/deploy-pages.yml` rebuilds `dashboard_data.json` and deploys the dashboard to GitHub Pages.

## Notes

The customer-voice area uses public review summaries surfaced in the supplied Google Play extraction. Where star scores are unavailable, the dashboard assigns a directional text signal from the supplied summary.

Data analysis & dashboard by Kelvin Irawan.


## Automated raw Google Play review collection

The project now includes `scrape_google_play.py`.

It reads all 25 app package IDs from the existing app metrics configuration and calls
the public Google Play review endpoint through `google-play-scraper`.

The collector uses `reviews_all()` with `Sort.NEWEST`, which follows continuation
pages until no additional public reviews are returned. It does not filter by star
rating, keyword, date range, or reviewer.

Every returned review is retained at the grain:

`package_id + review_id`

Outputs:
- `data/raw_reviews/runs/` — immutable per-run per-app raw CSVs
- `data/reviews_all_public_raw.csv` — cumulative deduplicated raw review archive
- `data/review_collection_status.csv` — per-app retrieval counts and date coverage
- `data/app_metrics_latest.csv` — latest high-level Play Store metadata
- `data/dashboard_data.json` — frontend dataset rebuilt after each run

## Daily schedule

Target schedule: **00:00 GMT+7 / Asia-Jakarta every day**.

Run `setup_daily_schedule.bat` once on Windows. Windows Task Scheduler uses the
computer's local timezone, so Windows should be set to:

`(UTC+07:00) Bangkok, Hanoi, Jakarta`

The task runs `run_daily_scraper.bat`.

If the machine is asleep at 00:00, the task is configured to start when available
only when using the PowerShell setup. For the BAT setup, Windows must be awake.


### Install the schedule

Double-click:

`setup_daily_schedule.bat`

It creates a Windows Task Scheduler task:
`Allo Bank Competitive Intelligence - Google Play Daily`

The task runs every day at **00:00 local time**. For 00:00 GMT+7, Windows must use the Jakarta/GMT+7 timezone. The task is configured to start when available if the computer was unavailable at the scheduled time.
