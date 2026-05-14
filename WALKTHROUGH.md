# TWO-DAY WALKTHROUGH — FROM ZERO TO DEPLOYED

This is the master guide. Follow it in order. Every account you'll need, every program to install, every command to run, every decision to make — in sequence.

**Total time:** ~6-8 focused hours over 2 days.
**Total cost:** ~$10 (one domain registration via Cloudflare, optional).

---

## CRITICAL CORRECTION ON DATA HISTORY

I want to set expectations honestly before you start:

**Reddit gives you newest ~1000 posts per subreddit, period.** For r/Minecraft that's roughly 2-3 weeks. For r/MinecraftMarketplace, more like 6+ months. Daily scraping accumulates *forward* history, not backward. You won't have 5 years of Reddit data in 2 days. You'll have whatever was in the newest 1000.

**feedback.minecraft.net is the actual time machine.** It's Zendesk-based and exposes a public API that lets you walk the entire post archive — years of history in one backfill run. The `feedback_scraper.py --backfill` command does exactly this. Plan for 30-60 minutes for that initial run; it'll pull thousands of posts and tens of thousands of comments spanning the platform's full history.

**Trustpilot, YouTube comments, App Store reviews** — same pattern. Each is a one-time backfill of historical depth, then incremental forward. Reddit is the exception, not the rule.

**For the interview demo specifically:** the feedback site backfill is your headline asset. The screenshot of "Minecraft community sentiment 2017-2026, with player-cap complaints tracked over time" comes from that single backfill, not from daily scraping.

---

## DAY 1 — BUILD THE DATA AND DASHBOARD (4-5 hours)

### Step 1: Install the software (20 min)

You need four things on your machine. Install in this order:

**1a. Python 3.11+** — https://www.python.org/downloads/
- Mac: download the installer, run it. Or `brew install python@3.11` if you have Homebrew.
- Windows: download the installer, **check "Add Python to PATH"** during install.
- Verify: open Terminal/PowerShell, run `python3 --version` (Mac) or `python --version` (Windows). Should print 3.11+.

**1b. Git** — https://git-scm.com/downloads
- Mac: probably already installed (`git --version` to check). Or `brew install git`.
- Windows: download the installer, accept defaults.

**1c. VS Code** — https://code.visualstudio.com/
- Both platforms: download installer, run it.
- After install: open VS Code, install the **Python extension** (Extensions panel → search "Python" → install the one from Microsoft).

**1d. Node.js (for Vercel CLI later)** — https://nodejs.org/
- Get the LTS version (currently 20.x).
- Verify: `node --version` should print v20+.

### Step 2: Create accounts (30 min)

You need five accounts. All free except optionally Cloudflare for the domain.

**2a. GitHub** — https://github.com/signup
- This is your code home. Pick a username carefully — see the naming section at the bottom of this doc before you commit.
- After signing up, verify your email.

**2b. Reddit** — https://www.reddit.com/register
- You need this to use the Reddit API.
- After signing up, go to https://www.reddit.com/prefs/apps
- Scroll down, click **"are you a developer? create an app..."**
- Fill in:
  - **name:** `MinecraftSentiment` (or any name)
  - **type:** select **script**
  - **description:** leave blank
  - **about url:** leave blank
  - **redirect uri:** `http://localhost:8080`
- Click "create app"
- You'll see your app with two values you need:
  - **client_id** — the random string just under "personal use script"
  - **client_secret** — the longer string next to "secret"
- Keep this tab open. You'll need these in step 4.

**2c. Hugging Face** — https://huggingface.co/join
- Free account. Pick a stable username (your name is safest).
- This is where the live dashboard will eventually deploy.

**2d. Vercel** — https://vercel.com/signup
- Sign up using your **GitHub account** ("Continue with GitHub"). This auto-links them.
- Free tier is plenty.

**2e. Cloudflare** — https://dash.cloudflare.com/sign-up *(optional — only if buying a domain)*
- Free account.
- You'll come back to this on Day 2 to buy the domain.

### Step 3: Set up the project folder (10 min)

Pick a folder for your work. Mine is at `~/Projects/`. Adjust paths as you go.

```bash
# Create a folder for everything
mkdir ~/Projects
cd ~/Projects

# You should have a folder called minecraft-sentiment-dashboard from this conversation.
# Move it into Projects (or copy the files into a folder by that name).

cd minecraft-sentiment-dashboard
```

Open this folder in VS Code: File → Open Folder → select `minecraft-sentiment-dashboard`.

You should see these files in the VS Code sidebar:
- `README.md`
- `WALKTHROUGH.md` (this file)
- `requirements.txt`
- `config.yaml`
- `minecraft_scraper.py`
- `feedback_scraper.py`
- `dashboard.py`
- `events.csv`
- `.gitignore`
- `landing-page/` (folder with `index.html` and `style.css`)

### Step 4: Install Python dependencies (10 min)

Open the integrated terminal in VS Code (Terminal → New Terminal). Run:

```bash
# Create a virtual environment (keeps dependencies isolated)
python3 -m venv .venv

# Activate it
# Mac/Linux:
source .venv/bin/activate
# Windows PowerShell:
.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

If anything fails, the most common cause is missing build tools. On Mac, run `xcode-select --install`. On Windows, install Visual C++ Build Tools.

### Step 5: Set Reddit credentials (5 min)

Create a file called `.env` in the project root (same folder as `README.md`). Put your Reddit credentials in it:

```bash
REDDIT_CLIENT_ID=your_actual_client_id_here
REDDIT_CLIENT_SECRET=your_actual_secret_here
REDDIT_USER_AGENT=MinecraftSentiment/0.1 by yourusername
```

Then export them into the terminal session:

```bash
# Mac/Linux:
export $(grep -v '^#' .env | xargs)

# Windows PowerShell:
Get-Content .env | ForEach-Object {
  if ($_ -match "^(.+?)=(.*)$") { [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process") }
}
```

Verify: `echo $REDDIT_CLIENT_ID` (Mac/Linux) or `echo $env:REDDIT_CLIENT_ID` (Windows) should print your client ID.

**The `.env` file is in `.gitignore` so it will never be committed to GitHub.** Keep it that way.

### Step 6: Run the FIRST scrape — feedback.minecraft.net backfill (45 min)

This is the big one. It walks the entire archive.

```bash
python feedback_scraper.py --backfill
```

You'll see logging like:
```
Page 1: scanned 100 posts, added 100 new posts (running total: 100 posts, ...)
Page 2: scanned 100 posts, added 100 new posts (running total: 200 posts, ...)
...
```

This runs for 30-60 minutes. Let it finish. Don't interrupt it. When it's done, you'll have a `data/processed/minecraft_sentiment.parquet` file containing the historical archive.

If you need to stop and resume later, that's fine — re-running picks up where it left off because of the dedupe logic.

### Step 7: Run the Reddit scrape (15 min)

```bash
python minecraft_scraper.py
```

This pulls the newest ~500 posts plus their comments from each configured subreddit. Should take 5-10 minutes. The records get added to the same parquet.

**Optional: Reddit historical backfill (30-60 min, only if you want more breadth).** The feedback.minecraft.net backfill in Step 6 is your headline historical asset and is enough on its own. If you also want a deep Reddit archive (e.g., for the "Top complaints on r/Minecraft 2019-2024" angle), there's a supplementary script that uses pullpush.io — a third-party Reddit archive — to grab years of topical Reddit posts and comments:

```bash
python historical_backfill.py
```

Caveats: pullpush.io is community-maintained, occasionally goes under maintenance, and isn't as authoritative as Mojang's own feedback site. Run this only if your time budget allows. The walkthrough does not depend on it.

### Step 8: Launch the dashboard locally (5 min)

```bash
streamlit run dashboard.py
```

Your default browser opens to `http://localhost:8501`. You should see:
- Top metrics (record count, sentiment, etc.)
- Time series chart spanning years (thanks to feedback.minecraft.net data)
- Pillar breakdown
- Verbatim quotes drawer at the bottom

**Play with the filters in the sidebar.** Make sure:
- The date range slider goes back several years
- "Top complaints" shows real-looking Minecraft player complaints
- Clicking "Open on Reddit ↗" or the feedback site link opens the original post

### Step 9: Curate events.csv (15 min)

Open `events.csv` and add real Minecraft milestones. The dashboard will overlay these on the timeline. Examples worth adding:

- Marketplace launch dates (skin packs launched 2017, world templates 2018)
- Marketplace Pass launch (2024)
- Realms major updates
- Pricing changes
- Specific feature complaints that got addressed

You can find these from Mojang's blog (https://www.minecraft.net/en-us/article) and the Minecraft Wiki. Pick 5-10 events. Quality beats quantity.

Save the file, refresh the dashboard, and the vertical event lines should appear on the timeline.

### Step 10: Take screenshots (10 min)

Before you go to bed, take screenshots of every dashboard view. These are your fallback for the interview if anything breaks.

- Hero metrics
- Timeline with events overlaid
- Pillar breakdown
- Verbatim quote drawer with a great complaint visible
- The same with "top praise" filter

Save these to a `screenshots/` folder. You'll use them in the interview presentation regardless of whether the live demo works.

**END OF DAY 1.** You have a working dashboard with real, deep, attributable data. The interview is technically winnable right here.

---

## DAY 2 — DEPLOY AND POLISH (3-4 hours)

### Step 11: Push the code to GitHub (20 min)

In VS Code terminal:

```bash
# Initialize git if not already
git init
git add .
git status   # verify .env is NOT in the list (it's gitignored)
git commit -m "Initial commit: Minecraft Player Pulse"
```

Create a new repo on GitHub:
1. Go to https://github.com/new
2. Repository name: `minecraft-player-pulse` (or whatever you want — see naming notes at the end)
3. **Public** (recruiters need to see it)
4. Do NOT initialize with README, .gitignore, or license — you already have them
5. Click "Create repository"

GitHub shows you commands. Use the "push an existing repository" block:

```bash
git remote add origin https://github.com/YOURUSERNAME/minecraft-player-pulse.git
git branch -M main
git push -u origin main
```

If GitHub asks for credentials, use a personal access token (Settings → Developer settings → Personal access tokens → Generate new token (classic) → check the `repo` scope). Use that token as the password.

### Step 12: Deploy the dashboard to Hugging Face Spaces (30 min)

Hugging Face Spaces hosts Streamlit dashboards for free.

1. Go to https://huggingface.co/new-space
2. **Space name:** `minecraft-player-pulse` (or similar)
3. **License:** MIT (or your preference)
4. **SDK:** select **Streamlit**
5. **Hardware:** CPU basic (free)
6. **Public**
7. Click "Create Space"

Hugging Face gives you a git URL like `https://huggingface.co/spaces/yourusername/minecraft-player-pulse`. Clone it locally:

```bash
cd ~/Projects
git clone https://huggingface.co/spaces/YOURUSERNAME/minecraft-player-pulse
cd minecraft-player-pulse
```

Now copy the files HF needs:

```bash
# From your other folder
cp ../minecraft-sentiment-dashboard/dashboard.py ./app.py
cp ../minecraft-sentiment-dashboard/requirements.txt ./
cp ../minecraft-sentiment-dashboard/events.csv ./
cp -r ../minecraft-sentiment-dashboard/data ./
```

(Note: Hugging Face Spaces expects the main file to be named `app.py`.)

Create a `README.md` for the Space:

```bash
cat > README.md << 'EOF'
---
title: Minecraft Player Pulse
emoji: 🎮
colorFrom: red
colorTo: green
sdk: streamlit
sdk_version: 1.30.0
app_file: app.py
pinned: false
---

# Minecraft Player Pulse

Rolling sentiment dashboard for Minecraft community feedback. Reddit + feedback.minecraft.net, classified by monetization pillar.
EOF
```

Push:

```bash
git add .
git commit -m "Initial dashboard deployment"
git push
```

HF will build the Space. Watch it at `https://huggingface.co/spaces/YOURUSERNAME/minecraft-player-pulse` — takes 3-5 minutes the first time. When ready, the dashboard is live and publicly viewable.

**Important: the data file is in the repo.** If you don't want your scraped data public, you'll need to re-scrape *inside* the Space using HF Secrets for your Reddit credentials. For interview prep, the simpler "data in repo" path is fine and fastest.

### Step 13: Buy your domain (15 min) — *optional but recommended*

If you want `yourname.com` (or whatever):

1. Log into Cloudflare → Registrar (left sidebar)
2. Search for the domain you want
3. Cloudflare charges at-cost (about $10/year for .com)
4. Add to cart, check out

The domain is live in minutes. DNS is automatically configured to Cloudflare's nameservers.

### Step 14: Deploy the landing page to Vercel (20 min)

The landing page is in `landing-page/`. It's pure HTML+CSS, deploys in seconds.

**14a. Customize the page first.** Open `landing-page/index.html` in VS Code. Find and replace:
- "Your Name" → your actual name (appears in title, brand, footer)
- `yourhandle` → your actual LinkedIn / GitHub handles
- `you@yourdomain.com` → your actual email
- Update the three card URLs to point to:
  - Card 1 → your Hugging Face Space URL
  - Card 2 → your GitHub repo URL
  - Card 3 → leave blank for now or link to the experiment doc once uploaded

**14b. Push to GitHub.** Create a new repo just for the landing page:

```bash
cd ~/Projects/minecraft-sentiment-dashboard/landing-page

git init
git add .
git commit -m "Landing page"

# Create a new GitHub repo at github.com/new called "personal-site" (or your name)
# Then:
git remote add origin https://github.com/YOURUSERNAME/personal-site.git
git branch -M main
git push -u origin main
```

**14c. Deploy on Vercel.**

1. Go to https://vercel.com/new
2. Import your `personal-site` GitHub repo
3. Framework Preset: **Other**
4. Click "Deploy"
5. In 30 seconds, you have a live URL like `personal-site-yourhandle.vercel.app`

**14d. Connect your domain.** In the Vercel project:
1. Settings → Domains
2. Add `yourname.com` (and also `www.yourname.com`)
3. Vercel gives you DNS records to add at Cloudflare. Copy them.
4. In Cloudflare → DNS for your domain → Add the records Vercel specified
5. Wait 1-5 minutes. Your domain now points to your landing page with SSL automatically.

### Step 15: Final scrape and final polish (30 min)

Back in your main project folder:

```bash
cd ~/Projects/minecraft-sentiment-dashboard
source .venv/bin/activate

# Get the freshest data
python minecraft_scraper.py

# Push the updated parquet to your HF Space
cd ~/Projects/minecraft-player-pulse
cp ../minecraft-sentiment-dashboard/data/processed/minecraft_sentiment.parquet ./data/processed/
git add data/
git commit -m "Refresh data"
git push
```

The HF Space rebuilds with fresh data.

### Step 16: Practice the interview demo (60 min)

Open your dashboard. Practice walking through it as if presenting:

1. Start with the high-level metrics — "I scraped about X records spanning Y years from public Minecraft channels."
2. Show the timeline — "Here's volume by sentiment over time. Notice the spike here when [event from events.csv] happened."
3. Filter by pillar — "If I narrow to Realms, you can see player-cap complaints have been steadily growing since 2022."
4. Drill into verbatim quotes — "Here's the specific complaint I cited earlier. You can click through and verify it at the source."
5. Hand over to the experiment backlog — "Each of these recurring complaints corresponds to one of my ranked experiments..."

**Have screenshots ready as backup.** Live demos fail. Always.

---

## NAMING — WHAT YOU CAN AND CAN'T CHANGE LATER

Short answer: yes, you can rename almost anything, but some things are stickier than others.

### Easy to change anytime
- **Domain name** — you can buy a new one for $10 and update DNS in 5 minutes. Old domain can be retired or redirected.
- **Vercel project name** — Settings → General → rename.
- **Repo content** — rename files, restructure folders, push.
- **Site copy** — edit the HTML, redeploy.
- **GitHub repo name** — Settings → rename. GitHub auto-redirects old URLs for ~30 days.

### Possible but with consequences
- **GitHub username** — can change in Settings. Auto-redirects for ~30 days, then your old username becomes available for anyone else to claim. All your repo URLs change. Anyone who linked to your repos sees broken links after the redirect window.
- **Hugging Face username** — similar; can change, but Space URLs change with you.
- **LinkedIn URL slug** — limited changes per year. Old URL doesn't auto-redirect.
- **Vercel team/account name** — possible but disruptive.

### Permanent or near-permanent
- **Reddit username** — cannot be changed. Ever. If you hate it, make a new account.
- **The domain string itself** — once you've bought yourname.com, you own yourname.com for the period you paid for. You can let it expire and buy a different one, but you've spent that $10.
- **Brand recognition and SEO equity** — every time you rename a public-facing handle, you reset the recognition clock. Cumulative cost over time.

### The strategy that survives renames

**Pick a stable handle for your "identity layer"** — typically your real name or a long-term professional handle. Use it consistently for:
- GitHub username
- Hugging Face username
- LinkedIn URL
- Email forwarding alias on your domain (you@yourname.com)

**Treat the "brand layer" as cheap and replaceable.** The actual project names, repo names, the word "lab," the dashboard's title — these can all change without consequence. If you decide in 6 months that "lab" should be "studio" or "workshop," rename the repos, update the landing page, deploy. Five minutes.

**Use subdomains for branding flexibility.** If your domain is `yourname.com`, you can have:
- `yourname.com` — main landing
- `lab.yourname.com` — the digital lab
- `mvp1.yourname.com` — first MVP
- `pulse.yourname.com` — the Player Pulse dashboard
- `blog.yourname.com` — if you start writing

Adding a subdomain is free and takes 1 minute in Cloudflare. If you later decide "lab" should be "research," you just rename the subdomain. The underlying identity (`yourname.com`) is unchanged.

**The single decision that matters most:** the domain you register today. Pick `yourname.com` if it's available (it's the universally safe choice). Avoid clever brand names that pigeonhole you. "DataScienceLab.com" is bad — it locks you to that identity. "FirstnameLastname.com" travels with you across career changes.

---

## QUICK REFERENCE — FILES AND WHERE THEY GO

```
~/Projects/
├── minecraft-sentiment-dashboard/    ← main project (private GitHub repo OK)
│   ├── minecraft_scraper.py
│   ├── feedback_scraper.py
│   ├── historical_backfill.py        ← optional supplementary Reddit backfill
│   ├── dashboard.py
│   ├── config.yaml
│   ├── events.csv
│   ├── requirements.txt
│   ├── .env                          ← never commit this
│   ├── data/processed/minecraft_sentiment.parquet
│   └── landing-page/                 ← copy out to deploy
│       ├── index.html
│       └── style.css
│
├── personal-site/                    ← Vercel deploys from this (public GitHub repo)
│   ├── index.html
│   └── style.css
│
└── minecraft-player-pulse/           ← Hugging Face deploys from this
    ├── app.py                        ← copy of dashboard.py
    ├── requirements.txt
    ├── events.csv
    ├── data/
    └── README.md                     ← HF Space metadata header
```

---

## IF SOMETHING BREAKS

**The scraper hangs or fails.** Check the logs. Most common: rate limit (wait 5 min and retry), bad Reddit credentials (re-export env vars), or Reddit API outage (wait, retry).

**The dashboard shows "No data yet."** The parquet file isn't at `data/processed/minecraft_sentiment.parquet`. Check that the scraper completed successfully.

**Streamlit won't start.** Make sure the venv is activated (`source .venv/bin/activate`). Re-run `pip install -r requirements.txt`.

**HF Space won't build.** Check the build logs on the Space page. Most common: missing dependency in requirements.txt, or Python version mismatch (HF defaults to 3.10; pin in your README header if needed).

**Vercel deploy fails.** Almost always a path issue. Make sure `index.html` is at the root of the repo, not in a subfolder.

**Domain doesn't resolve.** DNS propagation takes up to 5 minutes. If still broken after 30 minutes, check that the Cloudflare DNS records exactly match what Vercel asked for.

---

You've got this.
