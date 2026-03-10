# AI Job Scout

An autonomous AI-powered job discovery engine that scrapes multiple job boards in parallel, filters listings using Gemini 2.5 Flash, and delivers curated digest alerts to Slack — running on a fully automated schedule with zero manual intervention.

---

## What it does

You enter a search goal in plain English — like "founders office roles in Bengaluru" or "chief of staff at Series B startups in Mumbai" — and the engine handles everything else:

1. Discovers relevant job listings across LinkedIn, Indeed, Naukri and Google Jobs simultaneously
2. Scores each listing against your goal using Gemini 2.5 Flash (1-10 confidence score)
3. Filters out low-confidence matches silently — only high-signal roles reach Slack
4. Delivers one clean digest card per goal to a dedicated Slack channel
5. Logs every match (high and low confidence) to Google Sheets for reference
6. Runs automatically twice daily on Railway — no manual triggering needed

---

## Architecture

User Goal (natural language)
        |
        v
Discovery Layer — Gemini extracts search_term + location from goal
        |
        v
Parallel Scraping
LinkedIn -> Indeed -> Naukri -> Google Jobs (via python-jobspy)
Greenhouse -> Lever (via public ATS APIs)
        |
        v
AI Filter — Gemini 2.5 Flash scores each listing 1-10 against the goal
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

Multiple goals run in parallel threads simultaneously — total runtime stays constant regardless of how many goals you add.

---

## Features

- Natural language goals — no boolean search syntax, just describe what you want
- Multi-platform scraping — LinkedIn, Indeed, Naukri, Google Jobs, Greenhouse, Lever
- India-aware — automatically adds Naukri when an Indian city or "India" is detected in your goal
- Confidence scoring — Gemini scores every listing; configurable threshold controls what reaches Slack
- Digest format — all matches for a goal arrive as one Slack card, not individual pings
- Per-goal Slack channels — route each goal to its own dedicated channel
- Separate ops channel — run summaries go to #scout-ops, keeping job channels clean
- Daily cap — top N matches by score get Slacked; overflow is sheet-only
- Deduplication — jobs already seen in previous runs are never re-alerted
- Parallel execution — all goals run simultaneously via ThreadPoolExecutor
- Fully automated — Railway cron runs twice daily at 9am and 6pm IST

---

## Tech Stack

Component -> Technology

Job scraping -> python-jobspy
AI filtering -> Google Gemini 2.5 Flash
Scheduling -> Railway cron
Alerts -> Slack Incoming Webhooks
Logging -> Google Sheets via gspread
Language -> Python 3.11
Deployment -> Railway.app (Docker)

---

## Project Structure

job-scout/
scout.py — Main engine: scraping, AI filtering, Slack, Sheets
requirements.txt — Python dependencies
Dockerfile — Container config for Railway
railway.toml — Cron schedule config
.env — Local secrets (gitignored)
.env.example — Safe reference template (committed)
.gitignore — Blocks .env and credentials from git

---

## Environment Variables

GEMINI_API_KEY — Required — Google AI Studio API key
SLACK_WEBHOOK — Required — Default Slack channel webhook
SLACK_SUMMARY_WEBHOOK — Required — Webhook for #scout-ops summary channel
GOOGLE_CREDS_JSON — Required — Base64-encoded service account JSON
SHEET_NAME — Required — Google Sheet name for logging
SEARCH_GOALS — Required — Comma-separated list of search goals
GOAL_CHANNELS — Optional — Per-goal webhook routing
CONFIDENCE_THRESHOLD — Optional — Min score to reach Slack (default: 7)
MAX_SLACK_PER_GOAL — Optional — Max Slack alerts per goal per run (default: 10)
RESULTS_PER_SITE — Optional — Listings fetched per job board (default: 25)
HOURS_OLD — Optional — Max age of listings in hours (default: 72)

GOAL_CHANNELS format:
founders office:https://hooks.slack.com/WEBHOOK1|chief of staff:https://hooks.slack.com/WEBHOOK2

The keyword before : is matched case-insensitively against your goal string.

---

## Google Sheets Schema

A: Job ID | B: Title | C: Company | D: Location | E: Source | F: Goal | G: Score | H: Reason | I: Slacked? | J: Timestamp

---

## Local Setup

1. Clone the repo
git clone https://github.com/yourusername/job-scout
cd job-scout

2. Install dependencies
pip install -r requirements.txt

3. Copy and fill in environment variables
cp .env.example .env
Edit .env with your real credentials

4. Run locally
python scout.py

To generate the base64-encoded Google credentials string for GOOGLE_CREDS_JSON:
import base64
with open('your-service-account.json') as f:
    print(base64.b64encode(f.read().encode()).decode())

---

## Deployment (Railway)

1. Push this repo to GitHub
2. Go to railway.app -> New Project -> Deploy from GitHub
3. Add all required environment variables in the Variables tab
4. Railway builds the Docker container and starts the cron schedule automatically

The cron schedule in railway.toml is set to 30 3,12 * * * — which is 9am and 6pm IST daily.
To trigger a manual test run: Railway dashboard -> Deployments -> Redeploy.

---

## Roadmap

- Public web UI — enter a goal, get results without any setup
- Embedding layer (FAISS / ChromaDB) for semantic memory across runs
- Personalisation — improve scoring based on which roles you actually applied to
- Email digest option alongside Slack
- Multi-user support with individual goal profiles

---

## Why this exists

Finding niche roles like founders office or chief of staff requires manually searching across a dozen fragmented platforms daily — and most of the results are irrelevant. This project automates that entire workflow end-to-end, delivering only curated, AI-filtered matches on a schedule.

The goal is to make this publicly available so anyone navigating a frustrating job search can run their own instance and get the same leverage.

---

## Author

Advay Gupta
Manufacturing Engineering, BITS Pilani
LinkedIn -> www.linkedin.com/in/advaygupta6 | advaygupta74@gmail.com
