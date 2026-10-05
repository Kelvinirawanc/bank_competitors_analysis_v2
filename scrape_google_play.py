#!/usr/bin/env python3
"""
Allo Bank Competitive Intelligence Monitor
Google Play full public-review collector.

Scope:
    25 apps configured in the latest app metrics file, with the supplied
    5 Oct 2026 configuration as the fallback.

The collector:
    - retrieves ALL review records returned by google-play-scraper/reviews_all()
      for each configured app;
    - starts from the newest review and follows continuation pages until the
      public endpoint returns no more pages;
    - preserves the raw review fields returned by Google Play;
    - deduplicates by package_id + review_id;
    - maintains a cumulative master CSV;
    - writes an immutable per-run CSV snapshot;
    - captures current app-level Play Store metadata;
    - rebuilds dashboard_data.json after collection.

IMPORTANT:
    "All" here means all records retrievable from the public Google Play
    review endpoint at collection time. Google does not provide a public
    competitor lifetime export. For your own Allo Bank app, Play Console
    owner exports are the authoritative lifetime archive.

Run:
    py scrape_google_play.py

Optional:
    py scrape_google_play.py --lang id --country id
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from google_play_scraper import Sort, app as play_app, reviews_all


BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
RAW_DIR = DATA / "raw_reviews"
RUN_DIR = RAW_DIR / "runs"
METRICS_SOURCE_CURRENT = DATA / "app_metrics_latest.csv"
METRICS_SOURCE_LEGACY = DATA / "app_metrics_latest_web_2026-10-05.csv"
MASTER_REVIEWS = DATA / "reviews_all_public_raw.csv"
MASTER_SNAPSHOT = DATA / "review_collection_status.csv"
METRICS_CURRENT = DATA / "app_metrics_latest.csv"
RAW_DIR.mkdir(parents=True, exist_ok=True)
RUN_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_LANG = "id"
DEFAULT_COUNTRY = "id"
RUN_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")
RUN_TS = datetime.now(timezone.utc).isoformat()


def install_error_hint():
    print("\nIf google_play_scraper is missing, run:")
    print("    py -m pip install -r requirements.txt\n")


def load_apps() -> pd.DataFrame:
    source = METRICS_SOURCE_CURRENT if METRICS_SOURCE_CURRENT.exists() else METRICS_SOURCE_LEGACY
    if not source.exists():
        raise FileNotFoundError(f"Missing app config: {source}")
    df = pd.read_csv(source, dtype=str).fillna("")
    required = {"app_name", "package_id", "category", "benchmark_role", "source_url"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required app-config columns: {sorted(missing)}")
    df = df.drop_duplicates(subset=["package_id"]).reset_index(drop=True)
    if len(df) != 25:
        print(f"WARNING: expected 25 apps, found {len(df)}")
    return df


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def normalize_review_df(df: pd.DataFrame, app_row: pd.Series,
                        lang: str, country: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    rename = {
        "reviewId": "review_id",
        "userName": "reviewer_name",
        "userImage": "reviewer_image",
        "content": "review_text",
        "score": "rating",
        "thumbsUpCount": "thumbs_up_count",
        "reviewCreatedVersion": "review_created_version",
        "at": "review_date",
        "replyContent": "developer_reply",
        "repliedAt": "developer_reply_date",
    }
    out = df.rename(columns=rename).copy()

    # Preserve all library-returned fields, but normalize the common fields.
    out["app_name"] = app_row["app_name"]
    out["category"] = app_row["category"]
    out["benchmark_role"] = app_row["benchmark_role"]
    out["package_id"] = app_row["package_id"]
    out["source_url"] = app_row["source_url"]
    out["lang"] = lang
    out["country"] = country
    out["retrieved_at_utc"] = RUN_TS
    out["collection_run_date"] = RUN_DATE

    if "review_date" in out.columns:
        out["review_date"] = pd.to_datetime(
            out["review_date"], errors="coerce", utc=True
        ).dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Actual stable review ID from Google Play.
    if "review_id" not in out.columns:
        raise ValueError(
            f"No review_id returned for {app_row['package_id']}; refusing to "
            "create synthetic IDs because review-level deduplication would be unsafe."
        )

    out["review_id"] = out["review_id"].astype(str)

    # Stable raw-review grain.
    out = out.drop_duplicates(
        subset=["package_id", "review_id"], keep="last"
    ).reset_index(drop=True)

    return out


def collect_reviews(app_row: pd.Series, lang: str, country: str,
                    sleep_ms: int, retries: int) -> pd.DataFrame:
    package = app_row["package_id"]
    print(f"\n[REVIEWS] {app_row['app_name']} | {package}")

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            # reviews_all handles continuation tokens internally and continues
            # until the public endpoint stops returning additional reviews.
            records = reviews_all(
                package,
                lang=lang,
                country=country,
                sort=Sort.NEWEST,
                sleep_milliseconds=sleep_ms,
            )
            df = pd.DataFrame(records)
            return normalize_review_df(df, app_row, lang, country)
        except Exception as exc:
            last_error = exc
            print(f"  attempt {attempt}/{retries} failed: {exc}")
            if attempt < retries:
                time.sleep(min(30 * attempt, 120))

    raise RuntimeError(str(last_error))


def collect_app_metadata(app_row: pd.Series, lang: str, country: str,
                         retries: int) -> dict:
    package = app_row["package_id"]
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            meta = play_app(package, lang=lang, country=country)
            return {
                "snapshot_date": RUN_DATE,
                "retrieved_at_utc": RUN_TS,
                "app_name": app_row["app_name"],
                "category": app_row["category"],
                "benchmark_role": app_row["benchmark_role"],
                "package_id": package,
                "source_url": app_row["source_url"],
                "status": "OK",
                "title": meta.get("title"),
                "developer": meta.get("developer"),
                "score": meta.get("score"),
                "ratings": meta.get("ratings"),
                "reviews": meta.get("reviews"),
                "installs": meta.get("installs"),
                "updated": meta.get("updated"),
                "version": meta.get("version"),
                "description": meta.get("description"),
            }
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(15 * attempt, 60))

    return {
        "snapshot_date": RUN_DATE,
        "retrieved_at_utc": RUN_TS,
        "app_name": app_row["app_name"],
        "category": app_row["category"],
        "benchmark_role": app_row["benchmark_role"],
        "package_id": package,
        "source_url": app_row["source_url"],
        "status": f"ERROR: {last_error}",
    }


def save_run_file(df: pd.DataFrame, app_row: pd.Series, lang: str,
                  country: str) -> Path:
    filename = (
        f"{RUN_DATE}_{safe_name(app_row['package_id'])}_{lang}_{country}.csv"
    )
    path = RUN_DIR / filename
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def merge_master(new_frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = []

    if MASTER_REVIEWS.exists():
        old = pd.read_csv(MASTER_REVIEWS, dtype=str).fillna("")
        if not old.empty:
            frames.append(old)

    frames.extend([x for x in new_frames if not x.empty])

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True, sort=False).fillna("")

    # Actual review grain — never dedupe by reviewer/date/text.
    combined = combined.drop_duplicates(
        subset=["package_id", "review_id"], keep="last"
    ).reset_index(drop=True)

    if "review_date" in combined.columns:
        sort_date = pd.to_datetime(
            combined["review_date"], errors="coerce", utc=True
        )
        combined["_sort_date"] = sort_date
        combined = combined.sort_values(
            ["package_id", "_sort_date"], na_position="last"
        ).drop(columns=["_sort_date"])

    return combined


def write_status(status_rows: list[dict]):
    status = pd.DataFrame(status_rows)
    if MASTER_SNAPSHOT.exists():
        old = pd.read_csv(MASTER_SNAPSHOT, dtype=str).fillna("")
        status = pd.concat([old, status], ignore_index=True)

    # One status record per app per collection run.
    status = status.drop_duplicates(
        subset=["collection_run_date", "package_id", "lang", "country"],
        keep="last",
    )
    status.to_csv(MASTER_SNAPSHOT, index=False, encoding="utf-8-sig")


def build_dashboard():
    script = BASE / "build_static_data.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(BASE),
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError("build_static_data.py failed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", default=DEFAULT_LANG)
    parser.add_argument("--country", default=DEFAULT_COUNTRY)
    parser.add_argument("--sleep-ms", type=int, default=300)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()

    try:
        apps = load_apps()
    except Exception as exc:
        install_error_hint()
        raise

    all_frames = []
    status_rows = []
    metadata_rows = []

    print("=" * 72)
    print("ALLO BANK COMPETITIVE INTELLIGENCE — GOOGLE PLAY RAW REVIEW SCRAPER")
    print(f"Run UTC: {RUN_TS}")
    print(f"Storefront: {args.country} | Language: {args.lang}")
    print(f"Apps: {len(apps)}")
    print("=" * 72)

    for _, app_row in apps.iterrows():
        started = time.time()

        try:
            df = collect_reviews(
                app_row, args.lang, args.country,
                args.sleep_ms, args.retries
            )
            path = save_run_file(df, app_row, args.lang, args.country)
            all_frames.append(df)

            if df.empty:
                min_date = ""
                max_date = ""
            else:
                dates = pd.to_datetime(df["review_date"], errors="coerce", utc=True)
                min_date = dates.min().isoformat() if dates.notna().any() else ""
                max_date = dates.max().isoformat() if dates.notna().any() else ""

            status_rows.append({
                "collection_run_date": RUN_DATE,
                "retrieved_at_utc": RUN_TS,
                "app_name": app_row["app_name"],
                "package_id": app_row["package_id"],
                "lang": args.lang,
                "country": args.country,
                "status": "OK" if not df.empty else "NO_ROWS_RETURNED",
                "review_count_retrieved": len(df),
                "earliest_review_retrieved": min_date,
                "latest_review_retrieved": max_date,
                "run_seconds": round(time.time() - started, 2),
                "output_file": str(path.relative_to(BASE)),
            })
            print(
                f"  -> {len(df):,} reviews retrieved | "
                f"{min_date or 'n/a'} -> {max_date or 'n/a'}"
            )

        except Exception as exc:
            traceback.print_exc()
            status_rows.append({
                "collection_run_date": RUN_DATE,
                "retrieved_at_utc": RUN_TS,
                "app_name": app_row["app_name"],
                "package_id": app_row["package_id"],
                "lang": args.lang,
                "country": args.country,
                "status": f"ERROR: {exc}",
                "review_count_retrieved": 0,
                "earliest_review_retrieved": "",
                "latest_review_retrieved": "",
                "run_seconds": round(time.time() - started, 2),
                "output_file": "",
            })

        # Metadata is collected independently so one review failure doesn't
        # destroy the high-level snapshot.
        metadata_rows.append(
            collect_app_metadata(app_row, args.lang, args.country, args.retries)
        )

    master = merge_master(all_frames)
    if not master.empty:
        master.to_csv(MASTER_REVIEWS, index=False, encoding="utf-8-sig")

    write_status(status_rows)

    metadata_path = DATA / f"app_metrics_snapshot_{RUN_DATE}.csv"
    pd.DataFrame(metadata_rows).to_csv(
        metadata_path, index=False, encoding="utf-8-sig"
    )

    # Also expose the latest metadata under a stable filename.
    pd.DataFrame(metadata_rows).to_csv(
        METRICS_CURRENT, index=False, encoding="utf-8-sig"
    )

    print("\nMASTER REVIEW ARCHIVE")
    print(f"  File: {MASTER_REVIEWS}")
    print(f"  Unique reviews: {len(master):,}")
    print(f"  Status: {MASTER_SNAPSHOT}")
    print(f"  Metadata: {METRICS_CURRENT}")

    # Rebuild the dashboard only after the raw archive is written.
    build_dashboard()

    print("\nDONE.")
    print("The dashboard now uses the raw public-review archive, not the old")
    print("surfaced-review sample.")


if __name__ == "__main__":
    main()
