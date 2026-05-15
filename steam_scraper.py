"""
Steam Reviews Scraper for Minecraft Dungeons and Minecraft Legends.

Uses Steam's public appreviews API — no auth, no API key, cursor-paginated.
Documented at https://partner.steamgames.com/doc/store/getreviews

Why this source matters:
    Minecraft Dungeons and Legends are paid-DLC and live-service products
    sitting next to the core Minecraft franchise. Their Steam reviews are
    dominated by monetization friction — Dungeons' season-pass cadence,
    Legends' rough launch and content pricing. Direct evidence for
    MP-LISTING-01 (gameplay-image / expectation mismatch), MP-REFUND-01
    (buyer's remorse), and MP-REVIEW-01 (review-signal quality).

Output schema matches the Reddit and feedback-site scrapers.

Usage:
    python steam_scraper.py                       # both games, incremental
    python steam_scraper.py --game dungeons       # just Dungeons
    python steam_scraper.py --game legends        # just Legends
    python steam_scraper.py --max-pages 2         # bounded test run
    python steam_scraper.py --full                # ignore known IDs, refetch all
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


# Steam appids — verify at store.steampowered.com (number in the URL)
GAMES = {
    "dungeons": {"appid": 1672970, "name": "Minecraft Dungeons"},
    "legends":  {"appid": 1928870, "name": "Minecraft Legends"},
}

BASE_URL = "https://store.steampowered.com/appreviews"
USER_AGENT = "MinecraftSentiment/0.1 (research/portfolio)"
REQUEST_TIMEOUT = 30
PAUSE_BETWEEN_REQUESTS = 0.4  # well under Steam's ~10/sec rate limit
REVIEWS_PER_PAGE = 100         # Steam API max

MASTER_PATH = Path("data/processed/minecraft_sentiment.parquet")
RAW_DIR = Path("data/raw/steam")
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


# ---------- Steam API ----------

def fetch_review_batch(appid: int, cursor: str) -> dict | None:
    """
    Fetch one batch of reviews from Steam.

    Returns the raw JSON dict, or None on failure.
    Steam returns success=1 even for empty results; we check the reviews array.
    """
    url = f"{BASE_URL}/{appid}"
    params = {
        "json": 1,
        "language": "english",
        "filter": "recent",          # chronological, most recent first
        "cursor": cursor,
        "num_per_page": REVIEWS_PER_PAGE,
        "review_type": "all",
        "purchase_type": "all",
    }
    headers = {"User-Agent": USER_AGENT}

    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    log.warning(f"appid {appid}: non-JSON response, retrying...")
                    time.sleep(2 ** attempt)
                    continue
                if data.get("success") == 1:
                    return data
                log.warning(f"appid {appid}: success={data.get('success')}, retrying...")
            else:
                log.warning(f"appid {appid}: status {r.status_code}, retrying...")
        except requests.RequestException as e:
            log.warning(f"appid {appid}: request error {e}, retrying...")
        time.sleep(2 ** attempt)

    log.error(f"Failed to fetch appid {appid} cursor {cursor[:20]} after 3 attempts")
    return None


def parse_one_review(raw: dict, appid: int, game_name: str) -> dict | None:
    """Convert one Steam review JSON object into our common schema."""
    rec_id = raw.get("recommendationid")
    if not rec_id:
        return None

    body = (raw.get("review") or "").strip()
    if not body:
        return None

    # Use first 80 chars of body as title (Steam reviews don't have separate titles)
    title = body.split("\n")[0][:80].strip()

    voted_up = bool(raw.get("voted_up"))  # True = recommended, False = not recommended
    votes_up = int(raw.get("votes_up") or 0)       # how many people found this helpful
    votes_funny = int(raw.get("votes_funny") or 0)
    weighted_score = float(raw.get("weighted_vote_score") or 0)

    # Timestamp_created is unix seconds
    try:
        created_utc = pd.to_datetime(int(raw.get("timestamp_created", 0)), unit="s", utc=True)
    except (ValueError, TypeError):
        return None

    author = raw.get("author", {}) or {}
    playtime = int(author.get("playtime_forever") or 0)  # minutes

    url = f"https://steamcommunity.com/app/{appid}/recommended/{rec_id}/"

    return {
        "record_id": f"steam_{rec_id}",
        "source": "steam",
        "subreddit": f"steam-{game_name}",  # surfaces in dashboard's source filter
        "title": title,
        "body": body,
        "score": votes_up,                   # helpful-count, comparable to Reddit upvotes
        "voted_up": voted_up,                # Steam's recommended / not-recommended
        "playtime_minutes": playtime,
        "weighted_score": weighted_score,
        "created_utc": created_utc,
        "url": url,
        "author": "anonymous",
    }


def crawl_app(
    appid: int,
    game_name: str,
    known_ids: set[str],
    max_pages: int | None = None,
) -> list[dict]:
    """Walk through all review pages for one Steam app, collecting new records."""
    records: list[dict] = []
    cursor = "*"
    page = 1
    consecutive_known = 0

    while True:
        if max_pages and page > max_pages:
            log.info(f"  Reached --max-pages={max_pages}, stopping")
            break

        log.info(f"  Page {page} (cursor: {cursor[:30]}...)")
        data = fetch_review_batch(appid, cursor)
        if data is None:
            log.error(f"  Fetch failed — ending crawl for {game_name}")
            break

        reviews = data.get("reviews") or []
        if not reviews:
            log.info(f"  No more reviews — reached the end of {game_name}")
            break

        new_this_page = 0
        for raw in reviews:
            record = parse_one_review(raw, appid, game_name)
            if not record:
                continue
            if record["record_id"] in known_ids:
                consecutive_known += 1
                continue
            consecutive_known = 0
            records.append(record)
            new_this_page += 1

        log.info(f"  Parsed {len(reviews)} reviews, {new_this_page} new")

        # If we hit a whole page of already-known reviews, we've caught up
        if known_ids and new_this_page == 0 and consecutive_known >= len(reviews):
            log.info(f"  All reviews on this page already on disk — stopping incremental crawl")
            break

        next_cursor = data.get("cursor")
        if not next_cursor or next_cursor == cursor:
            log.info(f"  Cursor didn't advance — ending crawl for {game_name}")
            break
        cursor = next_cursor
        page += 1
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
    snapshot = raw_dir / f"steam_scrape_{stamp}.parquet"
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
        "--game",
        choices=["dungeons", "legends", "both"],
        default="both",
        help="Which game(s) to scrape (default: both)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Bound the crawl per game (default: walk until empty)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Ignore known record_ids and refetch everything",
    )
    args = parser.parse_args()

    config = load_config()
    pillar_keywords = config.get("pillar_keywords", {})

    # Determine which Steam IDs we already have, to skip refetching
    known_ids: set[str] = set()
    if MASTER_PATH.exists() and not args.full:
        existing = pd.read_parquet(MASTER_PATH)
        if "record_id" in existing.columns:
            known_ids = set(
                existing.loc[
                    existing["record_id"].str.startswith("steam_", na=False),
                    "record_id",
                ].tolist()
            )
        log.info(f"Already have {len(known_ids):,} Steam reviews on disk")

    games_to_scrape = ["dungeons", "legends"] if args.game == "both" else [args.game]

    all_new_records: list[dict] = []
    for game_key in games_to_scrape:
        spec = GAMES[game_key]
        log.info(f"Crawling {spec['name']} (appid {spec['appid']})...")
        records = crawl_app(
            appid=spec["appid"],
            game_name=game_key,
            known_ids=known_ids,
            max_pages=args.max_pages,
        )
        log.info(f"  Got {len(records):,} new reviews from {spec['name']}")
        all_new_records.extend(records)

    if not all_new_records:
        log.info("No new reviews to enrich.")
        return

    log.info(f"\nEnriching {len(all_new_records):,} new reviews...")
    df = enrich(all_new_records, pillar_keywords)
    save(df, MASTER_PATH, RAW_DIR)

    log.info(f"\nSentiment distribution of new Steam reviews:")
    log.info(df["sentiment_label"].value_counts().to_string())
    log.info(f"\nRecommendation distribution:")
    log.info(df["voted_up"].value_counts().to_string())
    log.info(f"\nDate range:")
    log.info(f"  {df['created_utc'].min()} -> {df['created_utc'].max()}")


if __name__ == "__main__":
    main()
