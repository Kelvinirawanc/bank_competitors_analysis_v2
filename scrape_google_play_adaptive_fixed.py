#!/usr/bin/env python3
"""
Allo Bank Competitive Intelligence Monitor
Adaptive Google Play collector.

The benchmark set remains controlled by data/app_metrics_latest*.csv, but the
Google Play package id is treated as a recoverable identifier rather than a
permanent truth:
    1) validate the configured package id;
    2) if it fails, search Google Play by the configured app name;
    3) rank candidates by title similarity and exact-name matches;
    4) only switch ids when the confidence threshold is passed;
    5) collect public reviews + current app metadata;
    6) rebuild data/dashboard_data.json.

This makes the collector resilient to an app/package migration while keeping
Kelvin's benchmark universe explicit and auditable.

Run locally:
    py scrape_google_play_adaptive.py --lang id --country id

Full public review archive:
    py scrape_google_play_adaptive.py --full --lang id --country id

Default mode collects up to 1,000 newest reviews per app. Use --full to attempt
to collect the full public review history; pages are written incrementally.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd
from google_play_scraper import (
    Sort,
    app as play_app,
    reviews as play_reviews,
    search as play_search,
)


BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
RAW_DIR = DATA / "raw_reviews"
RUN_DIR = RAW_DIR / "runs"
METRICS_SOURCE_CURRENT = DATA / "app_metrics_latest.csv"
METRICS_SOURCE_LEGACY = DATA / "app_metrics_latest_web_2026-10-05.csv"
MASTER_REVIEWS = DATA / "reviews_all_public_raw.csv"
MASTER_SNAPSHOT = DATA / "review_collection_status.csv"
METRICS_CURRENT = DATA / "app_metrics_latest.csv"
RESOLUTION_LOG = DATA / "app_resolution_registry.csv"

DATA.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)
RUN_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_LANG = "id"
DEFAULT_COUNTRY = "id"
DEFAULT_SLEEP_MS = 300
DEFAULT_RETRIES = 3
DEFAULT_RESOLUTION_THRESHOLD = 0.78
DEFAULT_MAX_REVIEWS_PER_APP = 1000
REVIEW_PAGE_SIZE = 200

RUN_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")
RUN_TS = datetime.now(timezone.utc).isoformat()



def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")


def norm_text(value: str | None) -> str:
    value = str(value or "").lower().replace("™", "")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def name_similarity(expected: str, actual: str) -> float:
    a = norm_text(expected)
    b = norm_text(actual)
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    at = set(a.split())
    bt = set(b.split())
    overlap = len(at & bt) / max(1, len(at))
    exact = 1.0 if a == b else 0.0
    return max(ratio, overlap * 0.92, exact)


def install_error_hint() -> None:
    print("\nIf dependencies are missing, run:")
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

    df = df.drop_duplicates(subset=["app_name"], keep="first").reset_index(drop=True)
    if len(df) != 25:
        print(f"WARNING: benchmark config contains {len(df)} apps; expected 25")
    return df


def validate_package(package_id: str, lang: str, country: str, retries: int) -> dict[str, Any] | None:
    if not package_id:
        return None
    for attempt in range(1, retries + 1):
        try:
            meta = play_app(package_id, lang=lang, country=country)
            return meta
        except Exception as exc:
            if attempt < retries:
                time.sleep(min(5 * attempt, 20))
            elif exc:
                return None
    return None


def resolve_package_id(
    app_row: pd.Series,
    lang: str,
    country: str,
    retries: int,
    threshold: float,
) -> tuple[pd.Series, dict[str, Any]]:
    row = app_row.copy()
    configured_id = str(row["package_id"]).strip()
    expected_name = str(row["app_name"]).strip()

    configured_meta = validate_package(configured_id, lang, country, retries)
    if configured_meta:
        resolved_id = configured_id
        confidence = name_similarity(expected_name, configured_meta.get("title", ""))
        # Existing working package ids are retained even when Play's display
        # title has small localization/branding differences.
        registry = {
            "run_date": RUN_DATE,
            "app_name": expected_name,
            "configured_package_id": configured_id,
            "resolved_package_id": resolved_id,
            "resolution_method": "configured_id_validated",
            "resolution_confidence": round(confidence, 4),
            "resolved_title": configured_meta.get("title", ""),
            "status": "VALID",
        }
        row["package_id"] = resolved_id
        row["source_url"] = f"https://play.google.com/store/apps/details?id={resolved_id}&hl={lang}"
        return row, registry

    candidates: list[dict[str, Any]] = []
    search_terms = [expected_name]
    clean_name = re.sub(r"\b(the|bank|mobile|app)\b", " ", expected_name, flags=re.I)
    clean_name = re.sub(r"\s+", " ", clean_name).strip()
    if clean_name and norm_text(clean_name) != norm_text(expected_name):
        search_terms.append(clean_name)

    for term in search_terms:
        try:
            results = play_search(
                term,
                lang=lang,
                country=country,
                n=15,
            )
        except Exception:
            results = []

        for candidate in results or []:
            package = candidate.get("appId") or candidate.get("packageId") or ""
            title = candidate.get("title") or ""
            if not package:
                continue
            score = name_similarity(expected_name, title)
            # Exact expected-name matches receive a strong bonus; this avoids
            # accidentally resolving to similarly named unrelated apps.
            if norm_text(title) == norm_text(expected_name):
                score = min(1.0, score + 0.18)
            candidates.append({
                "package_id": package,
                "title": title,
                "developer": candidate.get("developer") or "",
                "url": candidate.get("url") or f"https://play.google.com/store/apps/details?id={package}&hl={lang}",
                "score": round(min(score, 1.0), 4),
            })

        if candidates:
            break

    candidates.sort(key=lambda x: x["score"], reverse=True)
    best = candidates[0] if candidates else None

    if best and best["score"] >= threshold:
        row["package_id"] = best["package_id"]
        row["source_url"] = f"https://play.google.com/store/apps/details?id={best['package_id']}&hl={lang}"
        registry = {
            "run_date": RUN_DATE,
            "app_name": expected_name,
            "configured_package_id": configured_id,
            "resolved_package_id": best["package_id"],
            "resolution_method": "google_play_search",
            "resolution_confidence": best["score"],
            "resolved_title": best["title"],
            "resolved_developer": best["developer"],
            "status": "RESOLVED",
            "candidates_checked": len(candidates),
        }
        return row, registry

    registry = {
        "run_date": RUN_DATE,
        "app_name": expected_name,
        "configured_package_id": configured_id,
        "resolved_package_id": configured_id,
        "resolution_method": "unresolved_keep_configured",
        "resolution_confidence": best["score"] if best else 0,
        "resolved_title": best["title"] if best else "",
        "resolved_developer": best["developer"] if best else "",
        "status": "UNRESOLVED",
        "candidates_checked": len(candidates),
    }
    return row, registry


def resolve_all_apps(
    apps: pd.DataFrame,
    lang: str,
    country: str,
    retries: int,
    threshold: float,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    rows: list[pd.Series] = []
    registry: list[dict[str, Any]] = []

    print("\n[ADAPTIVE RESOLUTION]")
    for _, app_row in apps.iterrows():
        resolved, info = resolve_package_id(app_row, lang, country, retries, threshold)
        rows.append(resolved)
        registry.append(info)
        print(
            f"  {info['app_name']}: {info['resolution_method']} -> "
            f"{info['resolved_package_id']} (confidence={info['resolution_confidence']})"
        )
        time.sleep(0.15)

    resolved_df = pd.DataFrame(rows).fillna("")
    return resolved_df, registry


def normalize_review_df(
    df: pd.DataFrame,
    app_row: pd.Series,
    lang: str,
    country: str,
) -> pd.DataFrame:
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

    if "review_id" not in out.columns:
        raise ValueError(
            f"No stable review_id returned for {app_row['package_id']}; "
            "refusing unsafe synthetic review IDs."
        )

    out["review_id"] = out["review_id"].astype(str)
    return out.drop_duplicates(subset=["package_id", "review_id"], keep="last").reset_index(drop=True)


def collect_reviews(
    app_row: pd.Series,
    lang: str,
    country: str,
    sleep_ms: int,
    retries: int,
    max_reviews: int | None,
) -> tuple[Path, int, str, str]:
    package = app_row["package_id"]
    print(f"\n[REVIEWS] {app_row['app_name']} | {package}")
    output_path = RUN_DIR / (
        f"{RUN_DATE}_{safe_name(package)}_{safe_name(lang)}_{safe_name(country)}.csv"
    )
    # A rerun on the same UTC date should produce a clean snapshot, not append
    # duplicate data to a previous partial run.
    output_path.unlink(missing_ok=True)

    total_reviews = 0
    earliest_review = ""
    latest_review = ""
    continuation_token = None
    seen_tokens: set[str] = set()
    wrote_header = False

    while max_reviews is None or total_reviews < max_reviews:
        remaining = (
            REVIEW_PAGE_SIZE
            if max_reviews is None
            else min(REVIEW_PAGE_SIZE, max_reviews - total_reviews)
        )
        page: list[dict[str, Any]] = []
        next_token = None
        last_error: Exception | None = None

        for attempt in range(1, retries + 1):
            try:
                page, next_token = play_reviews(
                    package,
                    lang=lang,
                    country=country,
                    sort=Sort.NEWEST,
                    count=remaining,
                    continuation_token=continuation_token,
                )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                print(f"  page attempt {attempt}/{retries} failed: {exc}")
                if attempt < retries:
                    time.sleep(min(5 * attempt, 30))

        if last_error is not None:
            raise RuntimeError(f"Review page fetch failed: {last_error}") from last_error
        if not page:
            break
        if max_reviews is not None:
            # Some library versions reuse the previous page size from the
            # continuation token, so trim explicitly to the configured cap.
            page = page[: max_reviews - total_reviews]
        if not page:
            break

        page_df = normalize_review_df(pd.DataFrame(page), app_row, lang, country)
        if not page_df.empty:
            # Persist each page immediately. Never retain an app's full history
            # in a Python list or DataFrame.
            page_df.to_csv(
                output_path,
                index=False,
                mode="a" if wrote_header else "w",
                header=not wrote_header,
                encoding="utf-8",
            )
            wrote_header = True
            total_reviews += len(page_df)

            dates = pd.to_datetime(page_df["review_date"], errors="coerce", utc=True)
            if dates.notna().any():
                page_min = dates.min().strftime("%Y-%m-%dT%H:%M:%SZ")
                page_max = dates.max().strftime("%Y-%m-%dT%H:%M:%SZ")
                earliest_review = min(filter(None, [earliest_review, page_min]))
                latest_review = max(filter(None, [latest_review, page_max]))

        print(f"  saved {total_reviews:,} reviews so far")

        token_value = getattr(next_token, "token", None)
        if token_value is None:
            break
        token_signature = repr(token_value)
        if token_signature in seen_tokens:
            print("  stopping because Google Play repeated a pagination token")
            break
        seen_tokens.add(token_signature)
        continuation_token = next_token
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000)

    return output_path, total_reviews, earliest_review, latest_review


def collect_app_metadata(
    app_row: pd.Series,
    lang: str,
    country: str,
    retries: int,
) -> dict[str, Any]:
    package = app_row["package_id"]
    last_error: Exception | None = None
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


def merge_master_from_files(new_files: list[Path]) -> int:
    """Merge review CSVs in a disk-backed SQLite table instead of in RAM."""
    if not MASTER_REVIEWS.exists() and not new_files:
        return 0

    db_fd, db_name = tempfile.mkstemp(prefix="review_merge_", suffix=".sqlite3", dir=DATA)
    os.close(db_fd)
    temp_master = MASTER_REVIEWS.with_name(f"{MASTER_REVIEWS.name}.tmp")
    fieldnames: list[str] = []
    known_fields: set[str] = set()

    def register_fields(names: list[str] | None) -> None:
        for name in names or []:
            if name and name not in known_fields:
                known_fields.add(name)
                fieldnames.append(name)

    def ingest_csv(connection: sqlite3.Connection, path: Path) -> None:
        if not path.exists() or path.stat().st_size == 0:
            return
        batch: list[tuple[str, str, str, str]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            register_fields(reader.fieldnames)
            for raw_row in reader:
                row = {key: (value or "") for key, value in raw_row.items() if key}
                package_id = row.get("package_id", "")
                review_id = row.get("review_id", "")
                if not package_id or not review_id:
                    continue
                batch.append(
                    (
                        package_id,
                        review_id,
                        row.get("review_date", ""),
                        json.dumps(row, ensure_ascii=False),
                    )
                )
                if len(batch) >= 1000:
                    connection.executemany(
                        """INSERT INTO review_store(package_id, review_id, review_date, payload)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(package_id, review_id) DO UPDATE SET
                             review_date=excluded.review_date, payload=excluded.payload""",
                        batch,
                    )
                    connection.commit()
                    batch.clear()
        if batch:
            connection.executemany(
                """INSERT INTO review_store(package_id, review_id, review_date, payload)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(package_id, review_id) DO UPDATE SET
                     review_date=excluded.review_date, payload=excluded.payload""",
                batch,
            )
            connection.commit()

    total = 0
    try:
        with sqlite3.connect(db_name) as connection:
            connection.execute(
                """CREATE TABLE review_store (
                     package_id TEXT NOT NULL,
                     review_id TEXT NOT NULL,
                     review_date TEXT NOT NULL,
                     payload TEXT NOT NULL,
                     PRIMARY KEY (package_id, review_id)
                   )"""
            )
            if MASTER_REVIEWS.exists():
                ingest_csv(connection, MASTER_REVIEWS)
            for path in new_files:
                ingest_csv(connection, path)

            total = connection.execute("SELECT COUNT(*) FROM review_store").fetchone()[0]
            if not fieldnames:
                return total

            with temp_master.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                cursor = connection.execute(
                    "SELECT payload FROM review_store ORDER BY package_id, review_date"
                )
                for (payload,) in cursor:
                    row = json.loads(payload)
                    writer.writerow({name: row.get(name, "") for name in fieldnames})

        os.replace(temp_master, MASTER_REVIEWS)
        print(f"  merged {total:,} unique reviews into {MASTER_REVIEWS}")
        return total
    finally:
        if "connection" in locals():
            connection.close()
        if temp_master.exists():
            temp_master.unlink()
        if os.path.exists(db_name):
            os.remove(db_name)


def write_status(status_rows: list[dict[str, Any]]) -> None:
    status = pd.DataFrame(status_rows)
    if MASTER_SNAPSHOT.exists():
        old = pd.read_csv(MASTER_SNAPSHOT, dtype=str).fillna("")
        status = pd.concat([old, status], ignore_index=True)
    status = status.drop_duplicates(
        subset=["collection_run_date", "package_id", "lang", "country"],
        keep="last",
    )
    status.to_csv(MASTER_SNAPSHOT, index=False, encoding="utf-8-sig")


def write_resolution_registry(rows: list[dict[str, Any]]) -> None:
    current = pd.DataFrame(rows)
    if RESOLUTION_LOG.exists():
        old = pd.read_csv(RESOLUTION_LOG, dtype=str).fillna("")
        current = pd.concat([old, current], ignore_index=True)
    current = current.drop_duplicates(
        subset=["run_date", "app_name"], keep="last"
    )
    current.to_csv(RESOLUTION_LOG, index=False, encoding="utf-8-sig")


def build_dashboard() -> None:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Adaptive Allo Bank Google Play scraper")
    parser.add_argument("--lang", default=DEFAULT_LANG)
    parser.add_argument("--country", default=DEFAULT_COUNTRY)
    parser.add_argument("--sleep-ms", type=int, default=DEFAULT_SLEEP_MS)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument(
        "--max-reviews-per-app",
        type=int,
        default=DEFAULT_MAX_REVIEWS_PER_APP,
        help="Maximum newest reviews to collect per app in normal mode (default: 1000).",
    )
    parser.add_argument(
        "--resolution-threshold",
        type=float,
        default=DEFAULT_RESOLUTION_THRESHOLD,
        help="Minimum title-matching confidence before changing a package id.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Attempt to collect all available public reviews (slower; pages are saved incrementally).",
    )
    args = parser.parse_args()
    if args.max_reviews_per_app < 1:
        parser.error("--max-reviews-per-app must be at least 1")

    try:
        apps = load_apps()
    except Exception:
        install_error_hint()
        raise

    print("=" * 76)
    print("ALLO BANK COMPETITIVE INTELLIGENCE — ADAPTIVE GOOGLE PLAY SCRAPER")
    print(f"Run UTC: {RUN_TS}")
    print(f"Storefront: {args.country} | Language: {args.lang}")
    print(f"Apps: {len(apps)}")
    print(f"Resolution threshold: {args.resolution_threshold:.2f}")
    if args.full:
        print("Collection mode: full public review archive (incremental page writes)")
    else:
        print(f"Collection mode: up to {args.max_reviews_per_app:,} newest reviews per app")
    print("=" * 76)

    resolved_apps, resolution_rows = resolve_all_apps(
        apps, args.lang, args.country, args.retries, args.resolution_threshold
    )
    write_resolution_registry(resolution_rows)

    run_files: list[Path] = []
    status_rows: list[dict[str, Any]] = []
    metadata_rows: list[dict[str, Any]] = []

    for _, app_row in resolved_apps.iterrows():
        started = time.time()
        try:
            output_path, review_count, min_date, max_date = collect_reviews(
                app_row,
                args.lang,
                args.country,
                args.sleep_ms,
                args.retries,
                max_reviews=None if args.full else args.max_reviews_per_app,
            )
            if review_count > 0:
                run_files.append(output_path)

            status_rows.append({
                "collection_run_date": RUN_DATE,
                "retrieved_at_utc": RUN_TS,
                "app_name": app_row["app_name"],
                "package_id": app_row["package_id"],
                "lang": args.lang,
                "country": args.country,
                "status": "OK" if review_count > 0 else "NO_ROWS_RETURNED",
                "review_count_retrieved": review_count,
                "earliest_review_retrieved": min_date,
                "latest_review_retrieved": max_date,
                "run_seconds": round(time.time() - started, 2),
                "output_file": str(output_path.relative_to(BASE)),
            })
            print(
                f"  -> {review_count:,} reviews | "
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

        metadata_rows.append(
            collect_app_metadata(app_row, args.lang, args.country, args.retries)
        )

    master_count = merge_master_from_files(run_files)

    write_status(status_rows)

    metadata_path = DATA / f"app_metrics_snapshot_{RUN_DATE}.csv"
    metadata_df = pd.DataFrame(metadata_rows)
    metadata_df.to_csv(metadata_path, index=False, encoding="utf-8-sig")
    metadata_df.to_csv(METRICS_CURRENT, index=False, encoding="utf-8-sig")

    print("\nMASTER REVIEW ARCHIVE")
    print(f"  File: {MASTER_REVIEWS}")
    print(f"  Unique reviews: {master_count:,}")
    print(f"  Status: {MASTER_SNAPSHOT}")
    print(f"  Resolution registry: {RESOLUTION_LOG}")
    print(f"  Metadata: {METRICS_CURRENT}")

    build_dashboard()

    print("\nDONE — adaptive Google Play collection completed.")


if __name__ == "__main__":
    main()
