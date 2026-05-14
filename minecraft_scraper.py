"""
Minecraft Player Pulse — incremental community sentiment scraper.

Runs incrementally:
- Loads existing record IDs from the master parquet
- Fetches newest content per subreddit
- Skips anything already stored (dedupe by Reddit ID)
- Enriches with pillar classification + VADER sentiment
- Appends to master parquet, writes a timestamped raw snapshot

Designed to be safe to run on a cron — repeated runs only add new content.
"""

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import praw
import yaml
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ---------- Configuration ----------

def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_reddit_client() -> praw.Reddit:
    """Build a Reddit client from environment variables."""
    required = ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise EnvironmentError(
            f"Missing env vars: {missing}. "
            f"Register a script app at https://www.reddit.com/prefs/apps and "
            f"export REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET / REDDIT_USER_AGENT."
        )
    return praw.Reddit(
        client_id=os.getenv("REDDIT_CLIENT_ID"),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
        user_agent=os.getenv("REDDIT_USER_AGENT"),
    )


# ---------- Incremental state ----------

def load_existing_ids(master_path: Path) -> set:
    """Return the set of record_ids already stored — for dedupe."""
    if not master_path.exists():
        return set()
    df = pd.read_parquet(master_path, columns=["record_id"])
    ids = set(df["record_id"].tolist())
    log.info(f"Loaded {len(ids):,} existing record IDs from {master_path}")
    return ids


# ---------- Enrichment ----------

def classify_pillars(text: str, pillar_keywords: dict) -> list:
    """Return a list of pillar names whose keywords appear in the text."""
    text_lower = (text or "").lower()
    matches = []
    for pillar, keywords in pillar_keywords.items():
        if any(kw.lower() in text_lower for kw in keywords):
            matches.append(pillar)
    return matches if matches else ["Unclassified"]


def score_sentiment(text: str, analyzer: SentimentIntensityAnalyzer) -> tuple:
    """Return (compound_score, label) from VADER."""
    scores = analyzer.polarity_scores(text or "")
    compound = scores["compound"]
    if compound >= 0.05:
        label = "positive"
    elif compound <= -0.05:
        label = "negative"
    else:
        label = "neutral"
    return compound, label


# ---------- Scraping ----------

def scrape_subreddit(
    reddit: praw.Reddit,
    subreddit_name: str,
    existing_ids: set,
    posts_limit: int,
    comments_limit: int,
) -> list:
    """Scrape newest posts and their top-level comments, skipping known IDs."""
    log.info(f"r/{subreddit_name}: scanning newest {posts_limit} posts...")
    records = []
    new_posts = 0
    new_comments = 0
    seen_posts = 0

    try:
        subreddit = reddit.subreddit(subreddit_name)
        for submission in subreddit.new(limit=posts_limit):
            seen_posts += 1
            post_id = submission.id

            if post_id not in existing_ids:
                records.append({
                    "record_id": post_id,
                    "record_type": "post",
                    "subreddit": subreddit_name,
                    "author": str(submission.author) if submission.author else "[deleted]",
                    "created_utc": datetime.fromtimestamp(
                        submission.created_utc, tz=timezone.utc
                    ),
                    "title": submission.title,
                    "body": submission.selftext,
                    "url": f"https://reddit.com{submission.permalink}",
                    "score": submission.score,
                    "num_comments": submission.num_comments,
                    "parent_id": None,
                })
                new_posts += 1

            # Walk comments — even if the post is known, comments may be new
            try:
                submission.comments.replace_more(limit=0)
                for comment in submission.comments.list()[:comments_limit]:
                    if comment.id in existing_ids:
                        continue
                    records.append({
                        "record_id": comment.id,
                        "record_type": "comment",
                        "subreddit": subreddit_name,
                        "author": str(comment.author) if comment.author else "[deleted]",
                        "created_utc": datetime.fromtimestamp(
                            comment.created_utc, tz=timezone.utc
                        ),
                        "title": None,
                        "body": comment.body,
                        "url": f"https://reddit.com{comment.permalink}",
                        "score": comment.score,
                        "num_comments": None,
                        "parent_id": comment.parent_id,
                    })
                    new_comments += 1
            except Exception as e:
                log.warning(f"Could not load comments for {post_id}: {e}")

            time.sleep(0.5)  # polite delay between submissions

    except Exception as e:
        log.error(f"r/{subreddit_name} scrape failed: {e}")

    log.info(
        f"r/{subreddit_name}: scanned {seen_posts} posts, "
        f"added {new_posts} new posts and {new_comments} new comments"
    )
    return records


def enrich(records: list, pillar_keywords: dict) -> pd.DataFrame:
    """Add pillar classification, sentiment scores, and scrape timestamp."""
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    analyzer = SentimentIntensityAnalyzer()

    # Combine title + body so post titles influence classification too
    combined_text = (
        df["title"].fillna("").astype(str) + " " + df["body"].fillna("").astype(str)
    ).str.strip()

    df["pillars"] = combined_text.apply(lambda t: classify_pillars(t, pillar_keywords))
    df["pillars_str"] = df["pillars"].apply(lambda lst: ", ".join(lst))

    sentiment_results = combined_text.apply(lambda t: score_sentiment(t, analyzer))
    df["sentiment_compound"] = sentiment_results.apply(lambda r: r[0])
    df["sentiment_label"] = sentiment_results.apply(lambda r: r[1])

    df["scraped_at"] = datetime.now(timezone.utc)
    return df


# ---------- Storage ----------

def save(df: pd.DataFrame, master_path: Path, raw_dir: Path) -> None:
    """Write a timestamped snapshot and merge into the master parquet."""
    if df.empty:
        log.info("No new records to save.")
        return

    raw_dir.mkdir(parents=True, exist_ok=True)
    master_path.parent.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snapshot = raw_dir / f"scrape_{stamp}.parquet"
    df.to_parquet(snapshot, index=False)
    log.info(f"Snapshot written: {snapshot} ({len(df):,} records)")

    if master_path.exists():
        existing = pd.read_parquet(master_path)
        combined = pd.concat([existing, df], ignore_index=True)
        # Safety dedupe on record_id, keeping most recent enrichment
        combined = combined.drop_duplicates(subset=["record_id"], keep="last")
    else:
        combined = df

    combined.to_parquet(master_path, index=False)
    log.info(f"Master updated: {master_path} ({len(combined):,} total records)")


# ---------- Summary ----------

def print_summary(df: pd.DataFrame) -> None:
    if df.empty:
        return
    print("\n" + "=" * 50)
    print(f"SCRAPE SUMMARY — {len(df):,} new records")
    print("=" * 50)

    print(f"\nBy subreddit:")
    print(df["subreddit"].value_counts().to_string())

    print(f"\nBy sentiment:")
    print(df["sentiment_label"].value_counts().to_string())

    print(f"\nBy pillar (records can match more than one):")
    counts = {}
    for pillars in df["pillars"]:
        for p in pillars:
            counts[p] = counts.get(p, 0) + 1
    for p, c in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {p}: {c}")

    # Highlight top-scored negative comments — these are the "loud complaints"
    print(f"\nTop 5 loudest negative records (by upvotes):")
    top_neg = df[df["sentiment_label"] == "negative"].nlargest(5, "score")
    for _, row in top_neg.iterrows():
        snippet = (row["body"] or row["title"] or "")[:120].replace("\n", " ")
        print(f"  [{row['score']:>4} pts] r/{row['subreddit']}: {snippet}...")
    print()


# ---------- Main ----------

def main():
    config = load_config()
    master_path = Path(config["storage"]["master_file"])
    raw_dir = Path(config["storage"]["output_dir"])

    existing_ids = load_existing_ids(master_path)
    reddit = get_reddit_client()

    all_records = []
    for sub in config["subreddits"]:
        all_records.extend(
            scrape_subreddit(
                reddit,
                sub,
                existing_ids,
                posts_limit=config["scrape_limits"]["posts_per_subreddit"],
                comments_limit=config["scrape_limits"]["comments_per_post"],
            )
        )

    df = enrich(all_records, config["pillar_keywords"])
    save(df, master_path, raw_dir)
    print_summary(df)


if __name__ == "__main__":
    main()
