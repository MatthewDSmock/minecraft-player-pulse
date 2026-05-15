"""
fix_us_appstore.py — One-shot cleanup for orphaned US App Store records.

When the App Store scraper was run with --max-pages 2 --countries us as a
bounded test, those US records were ingested before the `country` column
schema was finalized. As a result, ~100 US records have country='—' instead
of country='us' and don't appear in the world map view.

This script:
  1. Removes those orphaned records (record_id starting with 'appstore_us_'
     but with country='—' or NaN)
  2. Then the user re-runs `python appstore_scraper.py --countries us --full`
     to pull a clean US dataset

Usage:
    python fix_us_appstore.py        # dry run, shows what would be removed
    python fix_us_appstore.py --apply # actually applies the fix
"""

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


MASTER_PATH = Path("data/processed/minecraft_sentiment.parquet")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually apply the fix (default: dry run)")
    args = parser.parse_args()

    if not MASTER_PATH.exists():
        log.error(f"Master parquet not found at {MASTER_PATH}")
        return

    df = pd.read_parquet(MASTER_PATH)
    log.info(f"Loaded {len(df):,} total records")

    # Identify the orphaned US records — record_id prefix 'appstore_us_'
    # but country missing or '—'
    is_us_id = df["record_id"].str.startswith("appstore_us_", na=False)
    if "country" in df.columns:
        country = df["country"].fillna("—")
        is_orphaned = is_us_id & ((country == "—") | (country.isna()) | (country == ""))
    else:
        log.warning("No 'country' column found — all US App Store rows are orphaned")
        is_orphaned = is_us_id

    orphan_count = int(is_orphaned.sum())
    log.info(f"Found {orphan_count} orphaned US App Store records")

    if orphan_count == 0:
        log.info("Nothing to clean up. You can now run:")
        log.info("    python appstore_scraper.py --countries us --full")
        return

    if not args.apply:
        log.info("DRY RUN — pass --apply to actually remove these records.")
        log.info("After applying, run:")
        log.info("    python appstore_scraper.py --countries us --full")
        return

    # Apply the fix
    cleaned = df[~is_orphaned].copy()
    log.info(f"Removing {orphan_count} orphaned records ({len(df):,} -> {len(cleaned):,})")
    cleaned.to_parquet(MASTER_PATH, index=False)
    log.info(f"Saved cleaned parquet to {MASTER_PATH}")
    log.info("")
    log.info("Now run:")
    log.info("    python appstore_scraper.py --countries us --full")
    log.info("That will pull a fresh US dataset with the country column populated correctly.")


if __name__ == "__main__":
    main()
