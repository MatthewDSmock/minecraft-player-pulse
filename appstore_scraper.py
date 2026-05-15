"""
App Store Reviews Scraper for Minecraft (iOS).

Uses Apple's public iTunes RSS customer reviews feed — no auth required.
Endpoint: https://itunes.apple.com/{country}/rss/customerreviews/page={N}/id={app_id}/sortby=mostrecent/json

Why this source matters:
    The iOS Minecraft app is dominated by the mobile-Bedrock player segment —
    a different demographic from console (kid-on-parents'-phone), surfaced
    in the player validation call. App Store reviews include heavy signal
    on parental cosmetic-spending complaints (MP-PARENT-01), in-app purchase
    friction (MP-REFUND-01), and mobile-specific UX issues.

Coverage approach:
    Apple limits each country to 10 pages × 50 reviews = ~500 per country.
    Different countries have independent review databases, so iterating
    across English-speaking markets (US, GB, CA, AU) extends coverage to
    ~2,000 reviews.

Output schema matches the Reddit and feedback-site scrapers.

Usage:
    python appstore_scraper.py                          # all default countries
    python appstore_scraper.py --countries us           # just US
    python appstore_scraper.py --countries us,gb,ca,au  # custom set
    python appstore_scraper.py --max-pages 2            # bounded test per country
    python appstore_scraper.py --full                   # ignore known IDs
"""

import argparse
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yaml
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


# Minecraft on iOS — App ID confirmed via apps.apple.com/us/app/minecraft/id479516143
MINECRAFT_IOS_APP_ID = 479516143
APP_NAME = "Minecraft"

# Countries to iterate — English-speaking markets where reviews are predominantly English
DEFAULT_COUNTRIES = ["us", "gb", "ca", "au", "ie", "nz"]

BASE_URL = "https://itunes.apple.com"
USER_AGENT = "MinecraftSentiment/0.1 (research/portfolio)"
REQUEST_TIMEOUT = 30
PAUSE_BETWEEN_REQUESTS = 1.0   # polite — Apple is fine with this
MAX_PAGES_PER_COUNTRY = 10     # Apple's hard cap

MASTER_PATH = Path("data/processed/minecraft_sentiment.parquet")
RAW_DIR = Path("data/raw/appstore")
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


# ---------- iTunes RSS API ----------

def fetch_page(country: str, page: int, app_id: int = MINECRAFT_IOS_APP_ID) -> dict | None:
    """Fetch one page of reviews from the iTunes RSS JSON feed."""
    url = (
        f"{BASE_URL}/{country}/rss/customerreviews"
        f"/page={page}/id={app_id}/sortby=mostrecent/json"
    )
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    log.warning(f"{country} page {page}: non-JSON response")
                    return None
            if r.status_code == 404:
                # Apple returns 404 when you exceed available pages
                return None
            log.warning(f"{country} page {page}: status {r.status_code}, retrying...")
        except requests.RequestException as e:
            log.warning(f"{country} page {page}: request error {e}, retrying...")
        time.sleep(2 ** attempt)

    log.error(f"Failed to fetch {country} page {page}")
    return None


def parse_entries(data: dict, country: str, app_id: int = MINECRAFT_IOS_APP_ID) -> list[dict]:
    """
    Parse review entries from the iTunes RSS JSON response.

    The first entry is often app metadata (no rating field), so we filter
    for entries that have im:rating present.
    """
    feed = (data or {}).get("feed", {}) or {}
    entries = feed.get("entry") or []

    # When there's only one entry, RSS returns a dict instead of a list
    if isinstance(entries, dict):
        entries = [entries]

    records = []
    for raw in entries:
        record = parse_one_entry(raw, country, app_id)
        if record:
            records.append(record)
    return records


def parse_one_entry(raw: dict, country: str, app_id: int) -> dict | None:
    """Convert one iTunes RSS entry into our common schema."""
    # Skip app-metadata entries (no rating field)
    rating_block = raw.get("im:rating")
    if not rating_block:
        return None

    try:
        rating = int(rating_block.get("label") or 0)
    except (ValueError, TypeError):
        return None
    if rating < 1 or rating > 5:
        return None

    id_block = raw.get("id") or {}
    review_id = id_block.get("label")
    if not review_id:
        return None

    title_block = raw.get("title") or {}
    title = (title_block.get("label") or "").strip()

    content_block = raw.get("content") or {}
    body = (content_block.get("label") or "").strip()

    if not title and not body:
        return None

    # Date — iTunes uses ISO 8601
    updated_block = raw.get("updated") or {}
    date_str = updated_block.get("label")
    try:
        created_utc = pd.to_datetime(date_str, utc=True)
    except Exception:
        log.warning(f"Could not parse date for review {review_id}: {date_str}")
        return None

    # Vote counts (rarely populated for App Store, but capture if present)
    vote_count = 0
    vote_block = raw.get("im:voteCount")
    if vote_block:
        try:
            vote_count = int(vote_block.get("label") or 0)
        except (ValueError, TypeError):
            pass

    # App version captured for diagnostics — not part of dashboard schema yet
    version_block = raw.get("im:version") or {}
    app_version = version_block.get("label", "")

    # App Store doesn't have per-review URLs; link to the app page
    url = f"https://apps.apple.com/{country}/app/minecraft/id{app_id}"

    return {
        "record_id": f"appstore_{country}_{review_id}",
        "source": "appstore",
        "subreddit": f"appstore-{country}",  # surfaces in dashboard's source filter
        "title": title,
        "body": body,
        "score": rating,              # 1-5 star rating, used as engagement proxy
        "appstore_stars": rating,
        "appstore_version": app_version,
        "vote_count": vote_count,
        "created_utc": created_utc,
        "url": url,
        "author": "anonymous",
        "country": country,
    }


def crawl_country(
    country: str,
    known_ids: set[str],
    max_pages: int | None = None,
) -> list[dict]:
    """Walk through all available pages for one country's App Store."""
    page_limit = min(max_pages or MAX_PAGES_PER_COUNTRY, MAX_PAGES_PER_COUNTRY)
    records: list[dict] = []

    for page in range(1, page_limit + 1):
        log.info(f"  Page {page}/{page_limit}...")
        data = fetch_page(country, page)
        if data is None:
            log.info(f"  No more pages available for {country.upper()}")
            break

        entries = parse_entries(data, country)
        if not entries:
            log.info(f"  No reviews on page {page} for {country.upper()}, stopping")
            break

        new_this_page = sum(1 for e in entries if e["record_id"] not in known_ids)
        log.info(f"  Parsed {len(entries)} reviews, {new_this_page} new")

        for e in entries:
            if e["record_id"] not in known_ids:
                records.append(e)

        # Incremental short-circuit: if all reviews on this page are known,
        # we've caught up (assuming RSS returns chronological order)
        if known_ids and new_this_page == 0:
            log.info(f"  All reviews on this page already on disk — stopping {country.upper()}")
            break

        time.sleep(PAUSE_BETWEEN_REQUESTS)

    return records


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
    snapshot = raw_dir / f"appstore_scrape_{stamp}.parquet"
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
        "--countries",
        type=str,
        default=",".join(DEFAULT_COUNTRIES),
        help=f"Comma-separated country codes (default: {','.join(DEFAULT_COUNTRIES)})",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help=f"Bound the crawl per country (default: {MAX_PAGES_PER_COUNTRY} = Apple's hard cap)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Ignore known record_ids and refetch everything",
    )
    args = parser.parse_args()

    countries = [c.strip().lower() for c in args.countries.split(",") if c.strip()]
    if not countries:
        log.error("No countries specified")
        return

    config = load_config()
    pillar_keywords = config.get("pillar_keywords", {})

    # Determine which App Store IDs we already have, to skip refetching
    known_ids: set[str] = set()
    if MASTER_PATH.exists() and not args.full:
        existing = pd.read_parquet(MASTER_PATH)
        if "record_id" in existing.columns:
            known_ids = set(
                existing.loc[
                    existing["record_id"].str.startswith("appstore_", na=False),
                    "record_id",
                ].tolist()
            )
        log.info(f"Already have {len(known_ids):,} App Store reviews on disk")

    all_new_records: list[dict] = []
    for country in countries:
        log.info(f"Crawling App Store / {country.upper()}...")
        records = crawl_country(
            country=country,
            known_ids=known_ids,
            max_pages=args.max_pages,
        )
        log.info(f"  Got {len(records):,} new reviews from {country.upper()}")
        all_new_records.extend(records)

    if not all_new_records:
        log.info("No new reviews to enrich.")
        return

    log.info(f"\nEnriching {len(all_new_records):,} new reviews...")
    df = enrich(all_new_records, pillar_keywords)
    save(df, MASTER_PATH, RAW_DIR)

    log.info(f"\nSentiment distribution of new App Store reviews:")
    log.info(df["sentiment_label"].value_counts().to_string())
    log.info(f"\nStar-rating distribution:")
    log.info(df["score"].value_counts().sort_index().to_string())
    log.info(f"\nCountry distribution:")
    log.info(df["country"].value_counts().to_string())
    log.info(f"\nDate range:")
    log.info(f"  {df['created_utc'].min()} -> {df['created_utc'].max()}")


if __name__ == "__main__":
    main()
