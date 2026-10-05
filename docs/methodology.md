# Methodology & Coverage

## Scope

Allo Bank™ plus 24 selected Indonesian banking and fintech applications across three peer categories:

- Digital Banks
- Conventional Banks
- Non-bank Competitors

## Source files

The supplied 5 October 2026 files provide the initial app universe and methodology context.
The automated collection path then updates the monitoring dataset from the public Google Play endpoint:

- `data/app_metrics_latest_web_2026-10-05.csv` — fallback app configuration and package IDs
- `data/app_metrics_latest.csv` — latest collected app-level Play Store metadata
- `data/reviews_all_public_raw.csv` — cumulative deduplicated raw public reviews
- `data/review_collection_status.csv` — per-app collection status and date coverage
- `data/raw_reviews/runs/` — immutable per-run raw review snapshots

## Front-end calculations

The dashboard calculates peer averages, ranking order, displayed review totals, category filters, topic counts, and customer-voice proportions in the browser from `data/dashboard_data.json`.

Compact values such as `69.2K`, `1.94M`, and `100M+` are converted to numeric values only for charting and aggregation. Display labels remain as supplied.

## Customer voice

Each collected public review is assigned a directional signal:

- **Positive** — praise, satisfaction, or clear benefit.
- **Mixed** — positive value and concern/friction appear together.
- **Neutral** — factual or unclear tone.
- **Concern** — complaint, failure, friction, or dissatisfaction.

The dashboard uses the collected star score when available. The raw review text is retained in the raw archive;
the browser dashboard renders selected customer-voice signals rather than exposing the full archive on every page.

## Interpretation

Google Play displayed ratings, review counts, download bands, and app-update dates can change by locale, cache, or crawl time. The dashboard therefore presents the supplied values as the current monitoring feed for this package rather than implying a Play Console lifetime export.

## Attribution

Data analysis & dashboard by Kelvin Irawan.


## Raw review collection

The customer-voice layer is now sourced from `data/reviews_all_public_raw.csv`,
not `reviews_surfaced_web_2026-10-05.csv`.

The collector requests all publicly retrievable review pages for every configured
package and preserves the Google Play `review_id`. No star-rating, keyword,
reviewer, or date filter is applied during collection.

The collection is cumulative and stores immutable per-run files under
`data/raw_reviews/runs/`.

The phrase "all reviews" means all records returned by the public review endpoint
at collection time. It is not an official Play Console lifetime export. Google
Play can also allow a user to edit an existing review, so the raw public record is
the review state returned at collection time rather than a revision history.

## Schedule

The Windows task is intended to execute daily at 00:00 Asia/Jakarta (GMT+7).
