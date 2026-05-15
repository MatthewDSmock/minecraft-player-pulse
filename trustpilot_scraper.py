"""
Trustpilot Scraper for Minecraft.

Pulls public reviews from trustpilot.com/review/www.minecraft.net.
Trustpilot's review pages embed all data as JSON in a __NEXT_DATA__ script tag,
which is far more reliable to parse than the rendered HTML.

Output schema matches the Reddit and feedback-site scrapers so the dashboard
sees them as one dataset.

Usage:
    python trustpilot_scraper.py                  # incremental (skip known IDs)
    python trustpilot_scraper.py --max-pages 5    # bound for testing
    python trustpilot_scraper.py --full           # full crawl, all pages

Why this source matters:
    Trustpilot's audience writes reviews when they're frustrated enough to
    seek out a complaint channel. The Minecraft Trustpilot page averages
    ~1.6/5 stars and is dominated by Realms subscription complaints —
    auto-renewal surprise, billing issues, support failures. This is the
    primary data source backing the RM-RETENTION-01 experiment.
"""

import argparse
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from curl_cffi.requests.exceptions import RequestException as CurlRequestException
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


BASE_URL = "https://www.trustpilot.com/review/www.minecraft.net"
# curl_cffi handles the TLS fingerprint via impersonate=; the User-Agent is set
# inside curl_cffi to match the impersonated browser, so we don't need to override it
BROWSER_IMPERSONATE = "chrome131"   # matches a real Chrome 131 TLS/JA3/HTTP2 fingerprint
REQUEST_TIMEOUT = 30
PAUSE_BETWEEN_PAGES = 2.0  # bumped up from 1.5 — Trustpilot is fingerprint-sensitive

MASTER_PATH = Path("data/processed/minecraft_sentiment.parquet")
RAW_DIR = Path("data/raw/trustpilot")
CONFIG_PATH = "config.yaml"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ---------- Config ----------

def load_config(path: str = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------- Fetch + Parse ----------

def fetch_page(page_num: int) -> str | None:
    """
    Fetch one Trustpilot review page using curl_cffi with Chrome TLS fingerprint
    impersonation. Trustpilot fingerprints at the TLS layer (JA3/JA4), so a
    plain `requests.get()` returns 403; curl_cffi mimics a real Chrome handshake.
    """
    url = BASE_URL if page_num == 1 else f"{BASE_URL}?page={page_num}"

    for attempt in range(3):
        try:
            r = curl_requests.get(
                url,
                impersonate=BROWSER_IMPERSONATE,
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                log.info(f"Page {page_num}: 404 — likely past the last page")
                return None
            log.warning(f"Page {page_num} returned status {r.status_code}, retrying...")
        except CurlRequestException as e:
            log.warning(f"Page {page_num} request error: {e}, retrying...")
        time.sleep(2 ** attempt)

    log.error(f"Failed to fetch page {page_num} after 3 attempts")
    return None


def extract_next_data(html: str) -> dict | None:
    """Pull the __NEXT_DATA__ JSON blob from a Trustpilot page's HTML."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        log.warning("No __NEXT_DATA__ script found on page")
        return None
    try:
        return json.loads(script.string)
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse __NEXT_DATA__ JSON: {e}")
        return None


def parse_reviews_from_data(data: dict) -> list[dict]:
    """
    Extract review records from the __NEXT_DATA__ structure.

    Trustpilot's structure is roughly:
        data.props.pageProps.reviews = [ {...}, {...} ]

    Each review has: id, title, text, rating, dates.publishedDate, etc.
    Schema can shift — we defensively probe a few likely paths.
    """
    page_props = (data or {}).get("props", {}).get("pageProps", {})

    # Primary location
    reviews = page_props.get("reviews")
    if not reviews:
        # Sometimes nested under businessUnit or similar
        bu = page_props.get("businessUnit", {}) or {}
        reviews = bu.get("reviews", {})
        if isinstance(reviews, dict):
            reviews = reviews.get("reviews", [])

    if not reviews or not isinstance(reviews, list):
        log.warning("No reviews array found in __NEXT_DATA__ — schema may have changed")
        return []

    records = []
    for r in reviews:
        try:
            record = parse_one_review(r)
            if record:
                records.append(record)
        except Exception as e:
            log.warning(f"Skipping malformed review: {e}")
            continue

    return records


def parse_one_review(r: dict) -> dict | None:
    """Convert one Trustpilot review JSON object into our schema."""
    review_id = r.get("id")
    if not review_id:
        return None

    title = (r.get("title") or "").strip()
    body = (r.get("text") or "").strip()
    if not title and not body:
        return None

    rating = int(r.get("rating") or 0)  # 1-5 stars

    # Date can be in r.dates.publishedDate or r.createdAt
    date_str = None
    dates = r.get("dates") or {}
    if isinstance(dates, dict):
        date_str = dates.get("publishedDate") or dates.get("submittedDate")
    if not date_str:
        date_str = r.get("createdAt")

    try:
        created_utc = pd.to_datetime(date_str, utc=True)
    except Exception:
        log.warning(f"Could not parse date '{date_str}' for review {review_id}")
        return None

    # Build the deep-link URL
    url = f"https://www.trustpilot.com/reviews/{review_id}"

    return {
        "record_id": f"tp_{review_id}",
        "source": "trustpilot",
        "subreddit": "trustpilot",  # for filter UI parity with Reddit data
        "title": title,
        "body": body,
        "score": rating,            # 1-5 star rating, used as engagement proxy
        "trustpilot_stars": rating, # preserve explicitly
        "created_utc": created_utc,
        "url": url,
        "author": "anonymous",      # we don't store reviewer names
    }


# ---------- Enrichment (matches feedback_scraper logic) ----------

def classify_pillars(text: str, pillar_keywords: dict) -> list:
    text_lower = (text or "").lower()
    matches = [
        pillar
        for pillar, kws in pillar_keywords.items()
        if any(kw.lower() in text_lower for kw in kws)
    ]
    return matches if matches else ["Unclassified"]


def score_sentiment(text: str, analyzer) -> tuple:
    scores = analyzer.polarity_scores(text or "")
    compound = scores["compound"]
    if compound >= 0.05:
        label = "positive"
    elif compound <= -0.05:
        label = "negative"
    else:
        label = "neutral"
    return compound, label


def enrich(records: list, pillar_keywords: dict) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    analyzer = SentimentIntensityAnalyzer()
    combined = (
        df["title"].fillna("").astype(str) + " " + df["body"].fillna("").astype(str)
    ).str.strip()
    df["pillars"] = combined.apply(lambda t: classify_pillars(t, pillar_keywords))
    df["pillars_str"] = df["pillars"].apply(lambda lst: ", ".join(lst))
    sentiment = combined.apply(lambda t: score_sentiment(t, analyzer))
    df["sentiment_compound"] = sentiment.apply(lambda r: r[0])
    df["sentiment_label"] = sentiment.apply(lambda r: r[1])
    df["scraped_at"] = datetime.now(timezone.utc)
    return df


# ---------- Save ----------

def save(df: pd.DataFrame, master_path: Path, raw_dir: Path) -> None:
    if df.empty:
        log.info("Nothing new to save.")
        return
    raw_dir.mkdir(parents=True, exist_ok=True)
    master_path.parent.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snapshot = raw_dir / f"trustpilot_scrape_{stamp}.parquet"
    df.to_parquet(snapshot, index=False)
    log.info(f"Snapshot: {snapshot} ({len(df):,} new records)")

    if master_path.exists():
        existing = pd.read_parquet(master_path)
        combined = pd.concat([existing, df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["record_id"], keep="last")
    else:
        combined = df

    combined.to_parquet(master_path, index=False)
    log.info(f"Master: {master_path} ({len(combined):,} total records)")


# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Bound the crawl for testing (default: walk until empty)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Force a full crawl from page 1 (ignored when --max-pages is set)",
    )
    args = parser.parse_args()

    config = load_config()
    pillar_keywords = config.get("pillar_keywords", {})

    # Determine which review IDs we already have, to skip refetching
    known_ids: set[str] = set()
    if MASTER_PATH.exists() and not args.full:
        existing = pd.read_parquet(MASTER_PATH)
        known_ids = set(
            existing.loc[
                existing["record_id"].str.startswith("tp_", na=False),
                "record_id",
            ].tolist()
        )
        log.info(f"Already have {len(known_ids):,} Trustpilot reviews on disk")

    all_new_records: list[dict] = []
    page = 1
    consecutive_empty = 0

    while True:
        if args.max_pages and page > args.max_pages:
            log.info(f"Reached --max-pages={args.max_pages}, stopping")
            break

        log.info(f"Fetching page {page}...")
        html = fetch_page(page)
        if html is None:
            log.info("Page fetch failed or returned 404 — ending crawl")
            break

        data = extract_next_data(html)
        if not data:
            log.warning(f"No __NEXT_DATA__ on page {page} — ending crawl")
            break

        records = parse_reviews_from_data(data)
        if not records:
            consecutive_empty += 1
            log.info(f"No reviews parsed on page {page} (consecutive empty: {consecutive_empty})")
            if consecutive_empty >= 2:
                log.info("Two consecutive empty pages — ending crawl")
                break
            page += 1
            time.sleep(PAUSE_BETWEEN_PAGES)
            continue

        consecutive_empty = 0
        new_records = [r for r in records if r["record_id"] not in known_ids]
        log.info(f"Page {page}: parsed {len(records)} reviews, {len(new_records)} new")

        if not new_records and known_ids:
            # We hit reviews we've already saved — stop (assumes chronological order)
            log.info("All reviews on this page already on disk — stopping incremental crawl")
            break

        all_new_records.extend(new_records)
        page += 1
        time.sleep(PAUSE_BETWEEN_PAGES)

    if not all_new_records:
        log.info("No new reviews found.")
        return

    log.info(f"Enriching {len(all_new_records):,} new reviews...")
    df = enrich(all_new_records, pillar_keywords)
    save(df, MASTER_PATH, RAW_DIR)

    # Quick sanity-check breakdown
    log.info(f"\nSentiment distribution of new Trustpilot reviews:")
    log.info(df["sentiment_label"].value_counts().to_string())
    log.info(f"\nStar-rating distribution:")
    log.info(df["score"].value_counts().sort_index().to_string())
    log.info(f"\nDate range:")
    log.info(f"  {df['created_utc'].min()} → {df['created_utc'].max()}")


if __name__ == "__main__":
    main()
