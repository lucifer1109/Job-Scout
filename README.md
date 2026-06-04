# AI Job Scout

An autonomous AI-powered job discovery engine that scrapes multiple job boards in parallel, filters listings using Claude (Anthropic), and delivers curated digest alerts to Slack — running on a fully automated schedule with zero manual intervention.

---

## Why I Built This

Job and opportunity hunting is genuinely painful. Finding niche roles — like founders office, chief of staff, or early-stage operator positions — means manually trawling through LinkedIn, Indeed, Naukri, and a dozen startup career pages every single day. Most results are irrelevant noise. The good ones get buried. And by the time you find them, the window to apply has often closed.

I wanted a fundamentally different experience: describe what you're looking for in plain English, and have a system go find it for you — across every platform simultaneously, filtered by actual relevance, delivered directly to you. No boolean search syntax. No tab-switching. No manual effort.

Job Scout is that system. You enter a goal like "founders office roles at Series B startups in Bengaluru" and it handles everything else — scraping, AI scoring, deduplication, and delivery — twice a day, automatically.

The longer-term goal is to make this publicly available so anyone navigating a frustrating job search can run their own instance and get the same leverage.

---

## What it does

You enter a search goal in plain English — like "founders office roles in Bengaluru" or "chief of staff at Series B startups in Mumbai" — and the engine handles everything else:

1. Discovers relevant job listings across LinkedIn, Indeed, Naukri and Google Jobs simultaneously
2. Scores each listing against your goal using Claude (1-10 confidence score)
3. Filters out low-confidence matches silently — only high-signal roles reach Slack
4. Delivers one clean digest card per goal to a dedicated Slack channel
5. Logs every match (high and low confidence) to Google Sheets for reference
6. Runs automatically twice daily — no manual triggering needed

---

## Architecture

```
User Goal (natural language)
        |
        v
Discovery Layer — Claude extracts search_term + location from goal
        |
        v
Parallel Scraping
LinkedIn -> Indeed -> Naukri -> Google Jobs (via python-jobspy)
Greenhouse -> Lever (via public ATS APIs)
        |
        v
AI Filter — Claude scores each listing 1-10 against the goal
        |
     ---+---
     |      |
Score >= 7  Score < 7
     |      |
     v      v
  Slack   Silent log
  Digest  (Sheet only)
     |      |
     ---+---
        |
        v
  Google Sheets (full log of all matches)
```

Multiple goals run in parallel threads simultaneously — total runtime stays constant regardless of how many goals you add.

---

## Features

- Natural language goals — no boolean search syntax, just describe what you want
- Multi-platform scraping — LinkedIn, Indeed, Naukri, Google Jobs, Greenhouse, Lever
- India-aware — automatically adds Naukri when an Indian city or "India" is detected in your goal
- Confidence scoring — Claude scores every listing; configurable threshold controls what reaches Slack
- Digest format — all matches for a goal arrive as one Slack card, not individual pings
- Per-goal Slack channels — route each goal to its own dedicated channel
- Separate ops channel — run summaries go to #scout-ops, keeping job channels clean
- Daily cap — top N matches by score get Slacked; overflow is sheet-only
- Deduplication — jobs already seen in previous runs are never re-alerted
- Parallel execution — all goals run simultaneously via ThreadPoolExecutor
- Fully automated — cron runs twice daily at 9am and 6pm IST

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Job scraping | python-jobspy |
| AI filtering | Claude claude-sonnet-4-5 (Anthropic) |
| Scheduling | Render Cron Job |
| Alerts | Slack Incoming Webhooks |
| Logging | Google Sheets via gspread |
| Language | Python 3.11 |
| Deployment | Render.com (Docker) |

---

## Project Structure

```
job-scout/
├── scout.py          — Main engine: scraping, AI filtering, Slack, Sheets
├── requirements.txt  — Python dependencies
├── Dockerfile        — Container config
├── render.yaml       — Render cron schedule config
├── .env              — Local secrets (gitignored)
└── .env.example      — Safe reference template (committed)
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | ✅ | Anthropic API key (console.anthropic.com) |
| `SLACK_WEBHOOK` | ✅ | Default Slack channel webhook |
| `SLACK_SUMMARY_WEBHOOK` | ✅ | Webhook for #scout-ops summary channel |
| `GOOGLE_CREDS_JSON` | ✅ | Base64-encoded service account JSON |
| `SHEET_NAME` | ✅ | Google Sheet name for logging |
| `SHEET_ID` | optional | Google Sheet ID (preferred over name — survives renames) |
| `SEARCH_GOALS` | ✅ | Comma-separated list of search goals |
| `GOAL_CHANNELS` | optional | Per-goal webhook routing |
| `CONFIDENCE_THRESHOLD` | optional | Min score to reach Slack (default: 7) |
| `MAX_SLACK_PER_GOAL` | optional | Max Slack alerts per goal per run (default: 10) |
| `RESULTS_PER_SITE` | optional | Listings fetched per job board (default: 25) |
| `HOURS_OLD` | optional | Max age of listings in hours (default: 72) |

`GOAL_CHANNELS` format:
```
founders office:https://hooks.slack.com/WEBHOOK1|chief of staff:https://hooks.slack.com/WEBHOOK2
```

---

## Google Sheets Schema

| A | B | C | D | E | F | G | H | I | J |
|---|---|---|---|---|---|---|---|---|---|
| Job ID | Title | Company | Location | Source | Goal | Score | Reason | Slacked? | Timestamp |

The header row is written automatically on first run if the sheet is empty.

---

## Google Sheets Setup

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or use an existing one)
3. Enable the **Google Sheets API** and **Google Drive API**
4. Go to **IAM & Admin → Service Accounts** → Create a service account
5. Give it a name (e.g. `job-scout-bot`), click through to finish
6. Click the service account → **Keys** tab → **Add Key** → **JSON** → download the file
7. Create a new Google Sheet (name it anything — you'll use the ID)
8. Copy the Sheet ID from the URL: `docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit`
9. Share the sheet with the service account email (the one that looks like `job-scout-bot@your-project.iam.gserviceaccount.com`) — give it **Editor** access
10. Encode your credentials:
    ```bash
    base64 -i your-service-account.json | tr -d '\n'
    ```
11. Set `GOOGLE_CREDS_JSON` to the output string and `SHEET_ID` to your sheet ID

---

## Local Setup

```bash
# 1. Clone the repo
git clone https://github.com/lucifer1109/Job-Scout
cd Job-Scout

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and fill in environment variables
cp .env.example .env
# Edit .env with your real credentials

# 4. Run locally
python scout.py
```

---

## Deployment (Render — replaces Railway)

Railway trial expired. Render is the recommended replacement — free tier, Docker support, cron scheduling built in.

### Steps

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) → **New** → **Cron Job**
3. Connect your GitHub repo
4. Set:
   - **Runtime**: Docker
   - **Schedule**: `30 3,12 * * *` (9am and 6pm IST)
   - **Command**: `python scout.py`
5. Add all required environment variables under the **Environment** tab
6. Click **Save** — Render will build the Docker image and run on schedule

To trigger a manual test: Render dashboard → your service → **Manual Run**.

> **Free tier note**: Render's free cron jobs have 750 run-hours/month — more than enough for twice-daily runs.

---

## Roadmap

- Public web UI — enter a goal, get results without any setup
- Embedding layer (FAISS / ChromaDB) for semantic memory across runs
- Personalisation — improve scoring based on which roles you actually applied to
- Email digest option alongside Slack
- Multi-user support with individual goal profiles

---

## Author

Advay Gupta
Manufacturing Engineering, BITS Pilani
[LinkedIn](https://www.linkedin.com/in/advaygupta6) | advaygupta74@gmail.com
