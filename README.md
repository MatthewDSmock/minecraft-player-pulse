# Minecraft Player Pulse

Rolling sentiment + pillar analysis of public Minecraft community feedback. Built for monetization insight: where is the player voice loudest, and how is the trend moving over time?

## What it does

- **Scrapes incrementally.** Each run pulls only new posts and comments since the last run. The master dataset grows append-only; nothing is overwritten.
- **Classifies by pillar.** Each record is tagged with the monetization pillar(s) it relates to — Marketplace, Realms, Creator on Demand — using keyword matching that's easy to extend.
- **Scores sentiment.** VADER compound score (-1 to +1) per record, with a categorical label (positive / neutral / negative).
- **Preserves verbatim text.** The full original quote, the author handle, the exact timestamp, and a direct URL back to the source are all stored on every record. When you cite a player concern, you can show the actual words — and the hiring manager can click through and verify.
- **Powers two dashboards.** Streamlit for the public-facing portfolio version; Power BI Desktop reads the same parquet file for stakeholder presentations.

## Why this architecture

The two requirements that shaped it:

**"Rolling — keep pulling new comments."** The scraper is dedupe-driven: it loads existing record IDs from the master parquet, then asks each source for newest content and skips anything already stored. Schedule it on a cron (GitHub Actions has a free 2000 min/month tier that handles this comfortably) and the dataset grows daily without intervention. The Reddit and YouTube APIs return records with their original creation timestamps, so once you've run the scraper a few times you can see months of history accumulate. Backfilling further back is possible per-source — Reddit's API caps at ~1000 newest per listing, but you can paginate by date with specialized queries.

**"Verbatim quotes preserved as customer reference."** The schema stores `body` (full original text), `author`, `url` (direct link), and `created_utc` (when the player wrote it) on every record. In the dashboard, every chart is filterable to its underlying quotes, and every quote is one click from its source. When you build an experiment case in the interview, you can pull the exact quote with attribution: *"This complaint from r/MinecraftMarketplace on March 14, 2024 — '[exact words]' — is one of 47 similar comments in the last 90 days."*

## Setup

### 1. Reddit API credentials (free, 5 minutes)
1. Log into Reddit, go to https://www.reddit.com/prefs/apps
2. Click "create another app" at the bottom
3. Choose **script**
4. Set redirect URI to `http://localhost:8080`
5. Copy the **client ID** (small text under the app name) and the **secret**

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Set environment variables
```bash
export REDDIT_CLIENT_ID="your_client_id"
export REDDIT_CLIENT_SECRET="your_secret"
export REDDIT_USER_AGENT="MinecraftSentiment/0.1 by yourusername"
```

(On Windows: `set REDDIT_CLIENT_ID=...` or use a `.env` file with python-dotenv.)

### 4. Run the scraper
```bash
python minecraft_scraper.py
```

First run pulls the newest ~500 posts per configured subreddit plus their top comments. Subsequent runs only fetch new content.

### 5. View the Streamlit dashboard
```bash
streamlit run dashboard.py
```

Opens at http://localhost:8501.

### 6. Connect Power BI Desktop
- Get Data → More → File → Parquet
- Point to `data/processed/minecraft_sentiment.parquet`
- Build whatever visuals you want; the schema is below

## Project structure

```
minecraft-sentiment-dashboard/
├── README.md
├── requirements.txt
├── config.yaml              # subreddits, pillar keywords, scrape limits
├── minecraft_scraper.py     # main scraper — run this on a schedule
├── dashboard.py             # Streamlit dashboard
└── data/
    ├── raw/                 # timestamped snapshots per scrape run (audit trail)
    └── processed/
        └── minecraft_sentiment.parquet  # the master dataset
```

## Schema

| Column | Type | Notes |
|---|---|---|
| `record_id` | str | Reddit's unique ID — primary dedupe key |
| `record_type` | str | `post` or `comment` |
| `subreddit` | str | e.g., `Minecraft` |
| `author` | str | Username or `[deleted]` |
| `created_utc` | datetime | When the user wrote it — the timestamp that matters for trend analysis |
| `title` | str | Post title only (null for comments) |
| `body` | str | Full original text — preserved verbatim |
| `url` | str | Direct link to the source post or comment |
| `score` | int | Reddit's upvote count — useful as a reach proxy |
| `num_comments` | int | For posts only |
| `parent_id` | str | For comments — links to parent post |
| `pillars` | list[str] | Matched pillars (a record can match more than one) |
| `pillars_str` | str | Comma-joined version for Power BI convenience |
| `sentiment_compound` | float | VADER score, range -1 to +1 |
| `sentiment_label` | str | `positive` / `neutral` / `negative` |
| `scraped_at` | datetime | When the scraper pulled it — separate from when the user wrote it |

## Showing experiment impact on the dashboard

The two timestamps (`created_utc` and `scraped_at`) let you do the thing you actually want: see complaint volume on a topic drop after a known change ships.

To make this most compelling, maintain a small `events.csv` alongside the parquet with manually-curated Minecraft events:

```csv
date,event,pillar
2024-06-13,Realms Plus 150+ rotating items launched,Realms
2024-11-21,Marketplace Pass standalone purchase removed,Marketplace
2025-03-15,Minecoins gift card promotion (holiday),Marketplace
```

The dashboard overlays these as vertical annotations on the time series, so the visual narrative becomes: *"Here's the complaint volume about X. Here's when Y changed. Here's what happened to volume after."* That's the storytelling layer that turns sentiment data into experiment-impact evidence.

## Extending to other sources

The scaffolding makes adding sources straightforward — each source needs a function that returns a list of dicts matching the schema:

- **feedback.minecraft.net** — no API; HTML scrape with BeautifulSoup. Pages have stable structure with post text, author, votes, and timestamps.
- **YouTube comments** — YouTube Data API v3 (free with API key). Pull from the official Minecraft channel + top Marketplace creator channels.
- **Trustpilot** — paid API, or HTML scrape with respect for their robots.txt.
- **App Store / Play Store reviews** — `google-play-scraper` (Python) is reliable for Android; iOS via `app-store-scraper` (Node) or the App Store Connect API.
- **Discord** — bots are heavily moderated on the official Minecraft Discord and likely blocked. Manual export of public channels is feasible; automated scraping is not.

## Automation (optional but recommended)

Save this as `.github/workflows/daily-scrape.yml`:

```yaml
name: Daily sentiment scrape
on:
  schedule:
    - cron: "0 6 * * *"  # 6am UTC daily
  workflow_dispatch:      # also runs on-demand
jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - run: python minecraft_scraper.py
        env:
          REDDIT_CLIENT_ID: ${{ secrets.REDDIT_CLIENT_ID }}
          REDDIT_CLIENT_SECRET: ${{ secrets.REDDIT_CLIENT_SECRET }}
          REDDIT_USER_AGENT: ${{ secrets.REDDIT_USER_AGENT }}
      - name: Commit updated data
        run: |
          git config user.name "github-actions"
          git config user.email "actions@github.com"
          git add data/
          git diff --staged --quiet || git commit -m "Daily scrape $(date -u +%Y-%m-%d)"
          git push
```

Add the three Reddit secrets at Settings → Secrets and variables → Actions → New repository secret.

## A note on rate limits and politeness

The scraper sleeps 0.5s between posts to stay polite to Reddit's API (well under the documented rate limits). Reddit's official limit is 60 requests/minute for OAuth-authenticated apps; we use far less than that. If you scale to many subreddits, watch the logs — PRAW will surface 429s if you cross the line.
