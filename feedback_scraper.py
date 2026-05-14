"""
Minecraft Feedback Site Scraper.

Pulls posts and comments from feedback.minecraft.net using the public Zendesk
Help Center API. Supports two modes:

    python feedback_scraper.py              # incremental (skip known IDs)
    python feedback_scraper.py --backfill   # walk the whole archive (years of history)

Output schema matches Reddit scraper so the dashboard sees them as one dataset.
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

BASE_URL = "https://feedback.minecraft.net/api/v2/community"
USER_AGENT = "MinecraftSentiment/0.1 (research/portfolio)"
REQUEST_TIMEOUT = 30
PAUSE_BETWEEN_POSTS = 0.3   # polite delay between post-detail fetches
PAUSE_BETWEEN_PAGES = 0.6   # polite delay between paginated list pages

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ---------- Config ----------

def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------- API ----------

def get_json(url: str, params: dict = None) -> dict:
    """GET a JSON endpoint with retries."""
    for attempt in range(3):
        try:
            r = requests.get(
                url,
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                log.warning(f"Rate limited (429). Sleeping {wait}s.")
                time.sleep(wait)
                continue
            log.warning(f"HTTP {r.status_code} for {url}")
            return {}
        except requests.RequestException as e:
            log.warning(f"Request failed (attempt {attempt + 1}): {e}")
            time.sleep(2 ** attempt)
    return {}


def fetch_posts_page(page: int = 1, per_page: int = 100) -> dict:
    return get_json(
        f"{BASE_URL}/posts.json",
        params={
            "page": page,
            "per_page": per_page,
            "sort_by": "created_at",
            "sort_order": "desc",
        },
    )


def fetch_comments(post_id: int) -> list:
    """Fetch all comments on a post (paginated)."""
    comments = []
    page = 1
    while True:
        data = get_json(
            f"{BASE_URL}/posts/{post_id}/comments.json",
            params={"page": page, "per_page": 100},
        )
        if not data:
            break
        batch = data.get("comments", [])
        comments.extend(batch)
        if not data.get("next_page"):
            break
        page += 1
        time.sleep(PAUSE_BETWEEN_PAGES)
    return comments


# ---------- Existing IDs ----------

def load_existing_ids(master_path: Path) -> set:
    if not master_path.exists():
        return set()
    df = pd.read_parquet(master_path, columns=["record_id"])
    return set(df["record_id"].tolist())


# ---------- Mapping to common schema ----------

def to_record(obj: dict, kind: str, parent_id: str = None) -> dict:
    """Map a Zendesk post or comment to our common record schema."""
    if kind == "post":
        return {
            "record_id": f"fb_post_{obj['id']}",
            "record_type": "feedback_post",
            "subreddit": "feedback.minecraft.net",  # reuse field; treat as source
            "author": str(obj.get("author_id") or "unknown"),
            "created_utc": pd.to_datetime(obj["created_at"], utc=True),
            "title": obj.get("title"),
            "body": obj.get("details") or "",
            "url": obj.get("html_url"),
            "score": int(obj.get("vote_sum") or 0),
            "num_comments": int(obj.get("comment_count") or 0),
            "parent_id": None,
        }
    else:  # comment
        return {
            "record_id": f"fb_comment_{obj['id']}",
            "record_type": "feedback_comment",
            "subreddit": "feedback.minecraft.net",
            "author": str(obj.get("author_id") or "unknown"),
            "created_utc": pd.to_datetime(obj["created_at"], utc=True),
            "title": None,
            "body": obj.get("body") or "",
            "url": obj.get("html_url"),
            "score": int(obj.get("vote_sum") or 0),
            "num_comments": None,
            "parent_id": parent_id,
        }


# ---------- Scrape orchestration ----------

def scrape(existing_ids: set, max_pages: int = None, fetch_comments_for_known: bool = False) -> list:
    """
    Walk the feedback site list pages until either:
      - we exhaust pages, or
      - we hit max_pages, or
      - every post on a page is already known AND fetch_comments_for_known is False

    Returns a list of records in the common schema.
    """
    records = []
    page = 1
    consecutive_known_pages = 0
    total_new_posts = 0
    total_new_comments = 0

    while True:
        if max_pages and page > max_pages:
            log.info(f"Reached max_pages={max_pages}, stopping.")
            break

        data = fetch_posts_page(page=page)
        if not data:
            log.info("Empty response, stopping.")
            break

        posts = data.get("posts", [])
        if not posts:
            log.info("No more posts.")
            break

        new_posts_on_page = 0
        for post in posts:
            post_record = to_record(post, kind="post")
            is_new_post = post_record["record_id"] not in existing_ids

            if is_new_post:
                records.append(post_record)
                new_posts_on_page += 1
                total_new_posts += 1

            # Fetch comments for new posts always; for known posts only if asked
            should_fetch_comments = is_new_post or fetch_comments_for_known
            if should_fetch_comments and post.get("comment_count", 0) > 0:
                comments = fetch_comments(post["id"])
                for c in comments:
                    c_record = to_record(c, kind="comment", parent_id=post_record["record_id"])
                    if c_record["record_id"] not in existing_ids:
                        records.append(c_record)
                        total_new_comments += 1
                time.sleep(PAUSE_BETWEEN_POSTS)

        log.info(
            f"Page {page}: scanned {len(posts)} posts, "
            f"added {new_posts_on_page} new posts "
            f"(running total: {total_new_posts} posts, {total_new_comments} comments)"
        )

        # Early exit for incremental mode: if 3 consecutive pages have no new posts,
        # we've caught up to what we already have.
        if new_posts_on_page == 0 and not fetch_comments_for_known:
            consecutive_known_pages += 1
            if consecutive_known_pages >= 3:
                log.info("3 consecutive pages with no new posts. Done.")
                break
        else:
            consecutive_known_pages = 0

        if not data.get("next_page"):
            log.info("Reached final page.")
            break

        page += 1
        time.sleep(PAUSE_BETWEEN_PAGES)

    return records


# ---------- Enrichment (same logic as Reddit scraper) ----------

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
    snapshot = raw_dir / f"feedback_scrape_{stamp}.parquet"
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


# ---------- CLI ----------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape feedback.minecraft.net into the common parquet store."
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Walk the full archive (years of history). May take 30-60 min.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Cap pages scanned (each page = 100 posts). Useful for testing.",
    )
    args = parser.parse_args()

    config = load_config()
    master_path = Path(config["storage"]["master_file"])
    raw_dir = Path(config["storage"]["output_dir"])

    existing_ids = load_existing_ids(master_path)
    log.info(f"Existing record IDs in master: {len(existing_ids):,}")
    log.info(f"Mode: {'BACKFILL (full archive)' if args.backfill else 'INCREMENTAL'}")

    records = scrape(
        existing_ids=existing_ids,
        max_pages=args.max_pages,
        fetch_comments_for_known=args.backfill,
    )

    if not records:
        log.info("No new records. Exiting.")
        return

    df = enrich(records, config["pillar_keywords"])
    save(df, master_path, raw_dir)

    # Summary
    print("\n" + "=" * 50)
    print(f"FEEDBACK SCRAPE — {len(df):,} new records")
    print("=" * 50)
    print(f"\nDate range of new records:")
    print(f"  Earliest: {df['created_utc'].min()}")
    print(f"  Latest:   {df['created_utc'].max()}")
    print(f"\nBy record type:")
    print(df["record_type"].value_counts().to_string())
    print(f"\nBy sentiment:")
    print(df["sentiment_label"].value_counts().to_string())
    print(f"\nBy pillar:")
    counts = {}
    for ps in df["pillars"]:
        for p in ps:
            counts[p] = counts.get(p, 0) + 1
    for p, c in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {p}: {c}")
    print()


if __name__ == "__main__":
    main()
