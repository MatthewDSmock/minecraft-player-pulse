# Minecraft Player Pulse

A rolling sentiment + pillar analysis of public Minecraft community feedback. Built for monetization insight: where is the player voice loudest, and how is the trend moving over time?

**Live dashboard:** [minecraft-player-pulse.streamlit.app](https://minecraft-player-pulse.streamlit.app)
**Project home:** [minecraft-player-pulse.vercel.app](https://minecraft-player-pulse.vercel.app)

---

## What it does

- **Aggregates 17,547 records across 15 years** of public Minecraft community feedback (2010-10-09 to 2026-05-13) from Reddit and Mojang's official feedback site.
- **Classifies by monetization pillar.** Each record is tagged with the pillar(s) it relates to — Marketplace, Realms, Creator on Demand — using keyword matching that's easy to extend.
- **Scores sentiment.** VADER compound score (-1 to +1) per record, with a categorical label (positive / neutral / negative).
- **Preserves verbatim text.** Full original quote, author, exact timestamp, and direct URL back to the source are stored on every record. When you cite a player concern, you can show the actual words — and anyone reading can click through and verify.
- **Updates rolling.** The scraper is dedupe-driven: it loads existing record IDs, asks each source for newest content, and skips anything already stored. Schedule it on a cron and the dataset grows daily without intervention.

## Architecture

Two principles shaped this:

**Code repo stays clean; data lives separately.** The Python code is in this repo. The 6.4 MB master parquet lives as a [GitHub Release asset](https://github.com/MatthewDSmock/minecraft-player-pulse/releases/latest), versioned independently. Anyone cloning this repo runs `py download_data.py` to fetch the current dataset, and the deployed Streamlit dashboard auto-downloads it on first boot.

**Verbatim quotes are first-class.** The schema stores `body` (full original text), `author`, `url` (direct link), and `created_utc` (when the player wrote it) on every record. In the dashboard, every chart is filterable to its underlying quotes, and every quote is one click from its source. The verbatim quote isn't a footnote — it's the evidence behind every claim.

## Data sources

| Source | Records | Auth required? |
|---|---|---|
| **feedback.minecraft.net** (Mojang's official feedback site) | ~14,500 | None |
| **Reddit historical archive** (via pullpush.io) | ~3,000 | None |
| **Reddit live incremental** (via PRAW) | configurable | Yes (Reddit script app) |
| Trustpilot, YouTube, App Store — *roadmap* | — | Varies |

## Quickstart

### Get the data and run the dashboard

```bash
# Clone and set up
git clone https://github.com/MatthewDSmock/minecraft-player-pulse.git
cd minecraft-player-pulse
python -m venv .venv
.\.venv\Scripts\Activate.ps1     # Windows
# source .venv/bin/activate      # Mac/Linux
pip install -r requirements.txt

# Fetch the current dataset (6.4 MB, from GitHub Releases)
python download_data.py

# Run the dashboard
streamlit run dashboard.py
```

Opens at `http://localhost:8501`.

### (Optional) Rescrape from source

The deployed dashboard pulls from a snapshot Release. If you want to regenerate the dataset yourself:

```bash
# Pulls multi-year archive from Mojang's feedback site — no auth needed
python feedback_scraper.py --backfill

# Pulls multi-year Reddit archive via pullpush.io — no auth needed
python historical_backfill.py
```

For incremental Reddit scraping (newest posts only), you need a free [Reddit script app](https://www.reddit.com/prefs/apps) and three env vars (`REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`). Then:

```bash
python minecraft_scraper.py
```

## Project structure

```
minecraft-player-pulse/
├── README.md
├── WALKTHROUGH.md           # Day-by-day build log with commentary
├── requirements.txt
├── config.yaml              # Subreddits, pillar keywords, scrape limits
├── events.csv               # Manually curated Minecraft events (annotation overlays)
│
├── feedback_scraper.py      # Mojang's feedback site — no auth required
├── historical_backfill.py   # Reddit historical via pullpush.io — no auth required
├── minecraft_scraper.py     # Reddit live incremental — requires Reddit API creds
├── download_data.py         # Fetches the parquet from GitHub Releases
├── dashboard.py             # Streamlit dashboard
│
├── .streamlit/
│   └── config.toml          # Theme: dark base, terracotta accent, serif headers
│
├── landing-page/            # Static HTML/CSS deployed via Vercel
│   ├── index.html
│   └── style.css
│
└── data/                    # Gitignored — populated by download_data.py or scrapers
    ├── raw/                 # Timestamped snapshots per scrape (audit trail)
    └── processed/
        └── minecraft_sentiment.parquet
```

## Schema

| Column | Type | Notes |
|---|---|---|
| `record_id` | str | Source's unique ID — primary dedupe key |
| `record_type` | str | `post`, `comment`, `feedback_post`, `feedback_comment` |
| `subreddit` | str | Source channel (subreddit name, or `feedback.minecraft.net`) |
| `author` | str | Username or `[deleted]` |
| `created_utc` | datetime | When the user wrote it — the timestamp that matters for trend analysis |
| `title` | str | Post title only (null for comments) |
| `body` | str | Full original text — preserved verbatim |
| `url` | str | Direct link to the source post or comment |
| `score` | int | Upvote count — useful as a community-agreement proxy (positive *or* negative) |
| `num_comments` | int | For posts only |
| `parent_id` | str | For comments — links to parent post |
| `pillars` | list[str] | Matched pillars (a record can match more than one) |
| `pillars_str` | str | Comma-joined version for Power BI / Tableau convenience |
| `sentiment_compound` | float | VADER score, range -1 to +1 |
| `sentiment_label` | str | `positive` / `neutral` / `negative` |
| `scraped_at` | datetime | When the scraper pulled it — distinct from when the user wrote it |

## Showing experiment impact on the dashboard

The two timestamps (`created_utc` and `scraped_at`) enable the analysis that actually matters: complaint volume on a topic dropping after a known change ships.

Maintain `events.csv` with curated Minecraft events:

```csv
date,event,pillar
2024-06-13,Realms Plus 150+ rotating items launched,Realms
2024-11-21,Marketplace Pass standalone purchase removed,Marketplace
2025-03-15,Minecoins gift card promotion (holiday),Marketplace
```

The dashboard overlays these as vertical annotations on the time series, so the narrative becomes: *"Here's the complaint volume about X. Here's when Y changed. Here's what happened to volume after."* That's the storytelling layer that turns sentiment data into experiment-impact evidence.

## Extending to other sources

Each source needs a function that returns a list of dicts matching the schema:

- **YouTube comments** — YouTube Data API v3 (free with API key). Official Minecraft channel + top Marketplace creator channels.
- **Trustpilot** — HTML scrape (or paid API). Realms reviews are especially rich in auto-renewal complaints.
- **App Store / Play Store reviews** — `google-play-scraper` (Python) for Android; iOS via `app-store-scraper` (Node) or the App Store Connect API.
- **Discord** — public Minecraft Discord bot scraping is heavily moderated and not recommended. Manual export of public channels is feasible; automated scraping is not.

## Daily automation (optional)

Save this as `.github/workflows/daily-scrape.yml` to run the scraper on a cron via GitHub Actions:

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
          python-version: "3.13"
      - run: pip install -r requirements.txt
      - run: python feedback_scraper.py
      - run: python minecraft_scraper.py
        env:
          REDDIT_CLIENT_ID: ${{ secrets.REDDIT_CLIENT_ID }}
          REDDIT_CLIENT_SECRET: ${{ secrets.REDDIT_CLIENT_SECRET }}
          REDDIT_USER_AGENT: ${{ secrets.REDDIT_USER_AGENT }}
```

For a recurring data refresh, the cleanest pattern is: scraper runs nightly → uploads new parquet as a new Release asset → deployed dashboard fetches the latest on next page load. This keeps the code repo small and data versioned independently.

## A note on rate limits and politeness

The scrapers sleep 0.5s between requests to stay polite to source APIs (well under documented rate limits). pullpush.io is a community-run archive — please be a good citizen if you fork this and run heavy queries.

---

## About this project

Built as part of an interview process for a senior data science role on a major game's monetization team. The repo is public because the architecture is more interesting than the data: a real-world demonstration of how to combine free-tier services (GitHub, Streamlit Cloud, Vercel) into a working multi-source data pipeline with a public dashboard, in a weekend, with $0 in hosting fees.

**Author:** [Matthew D. Smock](https://www.linkedin.com/in/matthewdsmock/) · [github.com/MatthewDSmock](https://github.com/MatthewDSmock)
