"""
Historical Reddit backfill using pullpush.io.

USE THIS ONCE to populate the master parquet with years of historical data,
then let minecraft_scraper.py handle daily incremental updates.

Why this exists:
    Reddit's official API .new() only returns ~1000 newest posts per subreddit.
    For r/Minecraft that's ~2 weeks of history. To get a multi-year view in
    a single run, we use pullpush.io — a third-party Reddit archive that has
    historical data going back years.

How it works:
    For each subreddit, runs a topical search for each pillar keyword. This
    gives us historically-filtered records (we don't want every post in
    r/Minecraft ever — we want Marketplace / Realms / Creator mentions).

Caveats:
    - pullpush.io is third-party and occasionally goes under maintenance.
      If you get errors, check https://pullpush.io status.
    - Pagination via 'before' timestamp; each query caps at 500 records and
      we keep paginating until exhausted or we hit max_per_query.
    - Be polite: 1.5s between requests. A full backfill across 5 subreddits
      and ~9 keywords each takes ~30-60 minutes.
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yaml
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

PULLPUSH_SUBMISSION_URL = "https://api.pullpush.io/reddit/search/submission/"
PULLPUSH_COMMENT_URL = "https://api.pullpush.io/reddit/search/comment/"

PAGE_SIZE = 500          # max per pullpush request
SLEEP_BETWEEN = 1.5      # seconds between requests (be polite)
MAX_PER_QUERY = 2000     # cap per keyword × subreddit to keep runtime reasonable
REQUEST_TIMEOUT = 30


# ---------- Helpers ----------

def load_config(path="config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_existing_ids(master_path: Path) -> set:
    if not master_path.exists():
        return set()
    df = pd.read_parquet(master_path, columns=["record_id"])
    return set(df["record_id"].tolist())


def classify_pillars(text: str, pillar_keywords: dict) -> list:
    text_lower = (text or "").lower()
    matches = [
        pillar for pillar, kws in pillar_keywords.items()
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


# ---------- Pullpush API ----------

def query_pullpush(endpoint: str, base_params: dict, max_records: int) -> list:
    """Paginate through pullpush results using 'before' timestamps."""
    collected = []
    params = base_params.copy()

    while len(collected) < max_records:
        try:
            resp = requests.get(endpoint, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
        except requests.exceptions.RequestException as e:
            log.error(f"Request failed: {e}. Sleeping 10s and continuing.")
            time.sleep(10)
            break
        except ValueError as e:
            log.error(f"JSON parse failed: {e}")
            break

        batch = payload.get("data", [])
        if not batch:
            break

        collected.extend(batch)
        log.info(f"    fetched {len(batch)} → total {len(collected)}")

        if len(batch) < params.get("size", PAGE_SIZE):
            break  # no more pages

        # Paginate by oldest created_utc
        oldest = min(r.get("created_utc", 0) for r in batch)
        if not oldest:
            break
        params["before"] = oldest
        time.sleep(SLEEP_BETWEEN)

    return collected[:max_records]


# ---------- Normalization ----------

def _safe_ts(value) -> float:
    """Coerce created_utc to a float. pullpush returns it as int, float, or str."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize_submission(rec: dict, subreddit: str) -> dict:
    return {
        "record_id": rec.get("id", ""),
        "record_type": "post",
        "subreddit": subreddit,
        "author": rec.get("author") or "[deleted]",
        "created_utc": datetime.fromtimestamp(
            _safe_ts(rec.get("created_utc")), tz=timezone.utc
        ),
        "title": rec.get("title", ""),
        "body": rec.get("selftext", "") or "",
        "url": f"https://reddit.com{rec.get('permalink', '')}",
        "score": rec.get("score", 0),
        "num_comments": rec.get("num_comments", 0),
        "parent_id": None,
    }


def normalize_comment(rec: dict, subreddit: str) -> dict:
    return {
        "record_id": rec.get("id", ""),
        "record_type": "comment",
        "subreddit": subreddit,
        "author": rec.get("author") or "[deleted]",
        "created_utc": datetime.fromtimestamp(
            _safe_ts(rec.get("created_utc")), tz=timezone.utc
        ),
        "title": None,
        "body": rec.get("body", "") or "",
        "url": f"https://reddit.com{rec.get('permalink', '')}",
        "score": rec.get("score", 0),
        "num_comments": None,
        "parent_id": str(rec.get("parent_id")) if rec.get("parent_id") is not None else None,
    }


# ---------- Main ----------

def main():
    config = load_config()
    master_path = Path(config["storage"]["master_file"])
    master_path.parent.mkdir(parents=True, exist_ok=True)

    existing_ids = load_existing_ids(master_path)
    log.info(f"Existing records in master: {len(existing_ids):,}")

    # Pick the top 2-3 most distinctive keywords per pillar to limit query count
    # while still capturing the main themes
    search_plan = {}
    for pillar, kws in config["pillar_keywords"].items():
        search_plan[pillar] = kws[:3]

    log.info(f"Search plan:")
    for pillar, terms in search_plan.items():
        log.info(f"  {pillar}: {terms}")

    new_records = []

    for subreddit in config["subreddits"]:
        for pillar, terms in search_plan.items():
            for term in terms:
                log.info(f"\nr/{subreddit} · {pillar} · '{term}'")

                # Submissions
                subs = query_pullpush(
                    PULLPUSH_SUBMISSION_URL,
                    {"subreddit": subreddit, "q": term, "size": PAGE_SIZE, "sort": "desc"},
                    max_records=MAX_PER_QUERY,
                )
                for s in subs:
                    rec_id = s.get("id")
                    if rec_id and rec_id not in existing_ids:
                        new_records.append(normalize_submission(s, subreddit))
                        existing_ids.add(rec_id)

                # Comments
                comms = query_pullpush(
                    PULLPUSH_COMMENT_URL,
                    {"subreddit": subreddit, "q": term, "size": PAGE_SIZE, "sort": "desc"},
                    max_records=MAX_PER_QUERY,
                )
                for c in comms:
                    rec_id = c.get("id")
                    if rec_id and rec_id not in existing_ids:
                        new_records.append(normalize_comment(c, subreddit))
                        existing_ids.add(rec_id)

    if not new_records:
        log.info("No new historical records collected. Master unchanged.")
        return

    log.info(f"\nNormalized {len(new_records):,} new records. Enriching...")

    df = pd.DataFrame(new_records)
    analyzer = SentimentIntensityAnalyzer()

    text = (
        df["title"].fillna("").astype(str) + " " + df["body"].fillna("").astype(str)
    ).str.strip()

    df["pillars"] = text.apply(lambda t: classify_pillars(t, config["pillar_keywords"]))
    df["pillars_str"] = df["pillars"].apply(lambda lst: ", ".join(lst))

    sent = text.apply(lambda t: score_sentiment(t, analyzer))
    df["sentiment_compound"] = sent.apply(lambda r: r[0])
    df["sentiment_label"] = sent.apply(lambda r: r[1])
    df["scraped_at"] = datetime.now(timezone.utc)

    # Merge into master
    if master_path.exists():
        existing_df = pd.read_parquet(master_path)
        combined = pd.concat([existing_df, df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["record_id"], keep="last")
    else:
        combined = df

    combined.to_parquet(master_path, index=False)

    print(f"\n{'='*50}")
    print(f"HISTORICAL BACKFILL COMPLETE")
    print(f"{'='*50}")
    print(f"New records added: {len(df):,}")
    print(f"Master total: {len(combined):,}")
    print(f"Date range: {df['created_utc'].min()} → {df['created_utc'].max()}")
    print(f"\nBy pillar:")
    counts = {}
    for pillars in df["pillars"]:
        for p in pillars:
            counts[p] = counts.get(p, 0) + 1
    for p, c in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {p}: {c:,}")
    print(f"\nNext step: run minecraft_scraper.py daily to keep current.")


if __name__ == "__main__":
    main()
