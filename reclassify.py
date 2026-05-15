"""
reclassify.py — One-shot script to re-run pillar classification on the master parquet.

Use this whenever config.yaml's pillar_keywords are expanded — existing records
were classified with the old keyword list and are frozen in the parquet.
Sentiment scoring is left alone (it's keyword-independent).

Usage:
    python reclassify.py
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml


MASTER_PATH = Path("data/processed/minecraft_sentiment.parquet")
CONFIG_PATH = "config.yaml"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def load_config(path: str = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def classify_pillars(text: str, pillar_keywords: dict) -> list:
    text_lower = (text or "").lower()
    matches = [
        pillar
        for pillar, kws in pillar_keywords.items()
        if any(kw.lower() in text_lower for kw in kws)
    ]
    return matches if matches else ["Unclassified"]


def main():
    if not MASTER_PATH.exists():
        log.error(f"Master parquet not found at {MASTER_PATH}")
        return

    config = load_config()
    pillar_keywords = config.get("pillar_keywords", {})

    log.info(f"Loading {MASTER_PATH}...")
    df = pd.read_parquet(MASTER_PATH)
    log.info(f"  {len(df):,} records loaded")

    # Show before-state
    before_counts = df["pillars_str"].fillna("").value_counts().head(10)
    log.info(f"\nBefore re-classification — top pillar combinations:")
    for combo, count in before_counts.items():
        log.info(f"  {combo or '(empty)':40s} {count:,}")

    log.info(f"\nRe-classifying with expanded keyword set...")
    combined = (
        df["title"].fillna("").astype(str) + " " + df["body"].fillna("").astype(str)
    ).str.strip()

    df["pillars"] = combined.apply(lambda t: classify_pillars(t, pillar_keywords))
    df["pillars_str"] = df["pillars"].apply(lambda lst: ", ".join(lst))
    df["reclassified_at"] = datetime.now(timezone.utc)

    # Show after-state
    after_counts = df["pillars_str"].fillna("").value_counts().head(10)
    log.info(f"\nAfter re-classification — top pillar combinations:")
    for combo, count in after_counts.items():
        log.info(f"  {combo or '(empty)':40s} {count:,}")

    # Coverage stats
    unclassified_share = (df["pillars_str"] == "Unclassified").mean() * 100
    log.info(f"\nUnclassified share: {unclassified_share:.1f}%")

    pillar_share = {}
    for p in ["Marketplace", "Realms", "CreatorOnDemand"]:
        pillar_share[p] = df["pillars_str"].fillna("").str.contains(
            p, na=False, regex=False
        ).mean() * 100
    log.info(f"\nPillar coverage (records tagged with each pillar):")
    for p, pct in pillar_share.items():
        log.info(f"  {p:20s} {pct:.1f}%")

    # Save
    df.to_parquet(MASTER_PATH, index=False)
    log.info(f"\nSaved updated parquet to {MASTER_PATH}")


if __name__ == "__main__":
    main()
