import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import json
import time
import re
import os
import base64
import threading
import feedparser
from concurrent.futures import ThreadPoolExecutor, as_completed
import anthropic
from jobspy import scrape_jobs
import pandas as pd
from datetime import datetime, timedelta

# ==============================================================
# CONFIGURATION
# ==============================================================

ANTHROPIC_API_KEY    = os.environ["ANTHROPIC_API_KEY"]
SHEET_NAME           = os.environ.get("SHEET_NAME", "job scraper tool master sheet")
SHEET_ID             = os.environ.get("SHEET_ID", "")
SLACK_WEBHOOK        = os.environ["SLACK_WEBHOOK"]
SLACK_SUMMARY_WEBHOOK = os.environ.get("SLACK_SUMMARY_WEBHOOK", SLACK_WEBHOOK)

RAW_GOALS    = os.environ.get("SEARCH_GOALS", "any role at a high growth seed or series A startup in Bengaluru or Gurgaon")
SEARCH_GOALS = [g.strip() for g in RAW_GOALS.split(",") if g.strip()]

CONFIDENCE_THRESHOLD = int(os.environ.get("CONFIDENCE_THRESHOLD", "6"))
MAX_SLACK_PER_GOAL   = int(os.environ.get("MAX_SLACK_PER_GOAL", "10"))
RESULTS_PER_SITE     = int(os.environ.get("RESULTS_PER_SITE", "25"))
HOURS_OLD            = int(os.environ.get("HOURS_OLD", "72"))

TARGET_LOCATIONS = ["Bengaluru", "Bangalore", "Gurgaon", "Gurugram", "Delhi NCR", "Noida", "India"]
TARGET_STAGES    = ["seed", "series a", "pre-seed", "angel"]

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ==============================================================
# GOOGLE SHEETS AUTH
# ==============================================================

def setup_sheets():
    creds_raw = os.environ.get("GOOGLE_CREDS_JSON")
    if not creds_raw:
        raise ValueError("GOOGLE_CREDS_JSON not set")
    try:
        creds_dict = json.loads(creds_raw)
    except json.JSONDecodeError:
        creds_dict = json.loads(base64.b64decode(creds_raw).decode())

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    gc = gspread.authorize(creds)

    sh = gc.open_by_key(SHEET_ID) if SHEET_ID else gc.open(SHEET_NAME)

    # Sheet 1: Jobs
    jobs_sheet = sh.sheet1
    if not jobs_sheet.row_values(1):
        jobs_sheet.append_row([
            "Job ID", "Title", "Company", "Location", "Source",
            "Goal", "Score", "Reason", "Slacked?", "Timestamp", "Applied?"
        ])

    # Sheet 2: Companies (for RAG memory)
    try:
        companies_sheet = sh.worksheet("Companies")
    except:
        companies_sheet = sh.add_worksheet(title="Companies", rows=1000, cols=10)
        companies_sheet.append_row([
            "Company", "Stage", "Domain", "Location", "Founders",
            "Why Interesting", "Source", "Funding Date", "Score", "Timestamp"
        ])

    return jobs_sheet, companies_sheet

sheet_lock = threading.Lock()

def safe_append_row(sheet, row):
    with sheet_lock:
        sheet.append_row(row)

# ==============================================================
# RAG MEMORY — reads past results to inform scoring
# ==============================================================

def get_rag_context(companies_sheet, jobs_sheet):
    """Pull recent high-scoring companies + applied jobs as context for Claude."""
    context_parts = []

    try:
        company_rows = companies_sheet.get_all_records()
        good_companies = [
            r for r in company_rows
            if isinstance(r.get("Score"), (int, float)) and r.get("Score", 0) >= 7
        ][-20:]
        if good_companies:
            names = [r["Company"] for r in good_companies]
            context_parts.append(f"Previously identified high-quality companies: {', '.join(names)}")
    except:
        pass

    try:
        job_rows = jobs_sheet.get_all_records()
        applied = [r for r in job_rows if str(r.get("Applied?", "")).lower() == "yes"][-10:]
        if applied:
            titles = [f"{r['Title']} at {r['Company']}" for r in applied]
            context_parts.append(f"Candidate has shown interest in: {', '.join(titles)}")
    except:
        pass

    return "\n".join(context_parts) if context_parts else ""

# ==============================================================
# FUNDING NEWS — free RSS sources
# ==============================================================

def fetch_funding_news():
    """Pull India startup funding news from free RSS feeds."""
    feeds = [
        "https://inc42.com/feed/",
        "https://entrackr.com/feed/",
        "https://yourstory.com/feed",
        "https://news.google.com/rss/search?q=india+startup+funding+seed+series+A&hl=en-IN&gl=IN&ceid=IN:en",
    ]
    companies = []
    seen = set()

    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:15]:
                title   = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                link    = entry.get("link", "")
                pub     = entry.get("published", "")

                # Only keep funding-related articles
                funding_keywords = ["funding", "raises", "raised", "seed", "series a", "investment", "crore", "million"]
                if not any(kw in title.lower() or kw in summary.lower() for kw in funding_keywords):
                    continue

                # Filter for target locations
                location_hit = any(
                    loc.lower() in title.lower() or loc.lower() in summary.lower()
                    for loc in TARGET_LOCATIONS
                )

                # Filter for target stages
                stage_hit = any(
                    stage in title.lower() or stage in summary.lower()
                    for stage in TARGET_STAGES
                )

                if not (location_hit and stage_hit):
                    continue

                key = title[:50]
                if key in seen:
                    continue
                seen.add(key)

                companies.append({
                    "title":   title,
                    "summary": summary[:500],
                    "url":     link,
                    "source":  feed_url.split("/")[2],
                    "date":    pub
                })
        except Exception as e:
            print(f"  ⚠️ Feed error {feed_url}: {e}")
            continue

    print(f"  📰 Funding news: {len(companies)} relevant articles")
    return companies

# ==============================================================
# YC COMPANIES — free, no API key needed
# ==============================================================

def fetch_yc_companies():
    """Fetch recent YC India companies from public API."""
    try:
        url = "https://api.ycombinator.com/v0.1/companies?batch=W24&batch=S24&country=India&limit=50"
        r   = requests.get(url, timeout=10)
        if r.status_code == 200:
            data      = r.json()
            companies = data.get("companies", [])
            print(f"  🚀 YC: {len(companies)} India companies found")
            return [
                {
                    "name":     c.get("name", ""),
                    "tagline":  c.get("one_liner", ""),
                    "url":      c.get("website", ""),
                    "batch":    c.get("batch", ""),
                    "location": c.get("city", ""),
                    "source":   "ycombinator"
                }
                for c in companies
                if any(loc.lower() in (c.get("city") or "").lower() for loc in ["bangalore", "bengaluru", "gurgaon", "delhi", "noida"])
            ]
    except Exception as e:
        print(f"  ⚠️ YC fetch error: {e}")
    return []

# ==============================================================
# JOB FETCHERS
# ==============================================================

def fetch_jobs_ats(targets):
    found_jobs = []
    for co in targets.get("greenhouse", []):
        try:
            r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{co}/jobs", timeout=10)
            if r.status_code == 200:
                for j in r.json().get("jobs", []):
                    found_jobs.append({
                        "id": f"gh_{j['id']}", "title": j["title"], "co": co,
                        "url": j["absolute_url"], "loc": j["location"]["name"], "source": "greenhouse"
                    })
        except:
            continue
    for co in targets.get("lever", []):
        try:
            r = requests.get(f"https://api.lever.co/v0/postings/{co}", timeout=10)
            if r.status_code == 200:
                for j in r.json():
                    found_jobs.append({
                        "id": f"lv_{j['id']}", "title": j["text"], "co": co,
                        "url": j["hostedUrl"], "loc": j["categories"].get("location", "Unknown"),
                        "source": "lever"
                    })
        except:
            continue
    return found_jobs


def fetch_jobs_jobspy(search_term, location):
    found_jobs = []
    sites = ["indeed", "linkedin", "google", "naukri"]
    print(f"  🌐 Scraping {sites} for '{search_term}' in '{location}'...")
    try:
        jobs_df = scrape_jobs(
            site_name=sites,
            search_term=search_term,
            location=location,
            results_wanted=RESULTS_PER_SITE,
            hours_old=HOURS_OLD,
            country_indeed="india",
        )
        if jobs_df is not None and not jobs_df.empty:
            for _, row in jobs_df.iterrows():
                raw_id = str(row.get("id", f"{row.get('title','')}_{row.get('company','')}"))
                job_id = f"jsp_{re.sub(r'[^a-zA-Z0-9]', '_', raw_id)[:40]}"
                found_jobs.append({
                    "id":     job_id,
                    "title":  str(row.get("title", "Unknown")),
                    "co":     str(row.get("company", "Unknown")),
                    "url":    str(row.get("job_url", "#")),
                    "loc":    str(row.get("location", location)),
                    "source": str(row.get("site", "jobspy")),
                })
            print(f"  ✅ JobSpy returned {len(found_jobs)} listings")
        else:
            print(f"  ⚠️ JobSpy returned 0 results")
    except Exception as e:
        print(f"  ❌ JobSpy error: {e}")
    return found_jobs

# ==============================================================
# CLAUDE HELPER
# ==============================================================

def call_claude(system: str, user: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            msg = claude.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": user}]
            )
            return msg.content[0].text
        except anthropic.RateLimitError:
            wait = 30 * (attempt + 1)
            print(f"  ⏳ Rate limit. Waiting {wait}s...")
            time.sleep(wait)
        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                wait = 20 * (attempt + 1)
                print(f"  ⏳ Overloaded. Waiting {wait}s...")
                time.sleep(wait)
            else:
                raise e
    raise RuntimeError("Claude API failed after all retries")

# ==============================================================
# COMPANY INTELLIGENCE — score funding news via Claude
# ==============================================================

def score_companies_from_news(news_items: list, rag_context: str, companies_sheet) -> list:
    """Use Claude to extract and score companies from funding news."""
    if not news_items:
        return []

    system = (
        "You are a startup analyst helping a sharp 22-year-old from BITS Pilani find the best early-stage "
        "startup to join in India. He wants high learning, high impact, smart founders, strong growth trajectory. "
        "He does not care about work-life balance. Pay is secondary to opportunity. "
        "Target locations: Bengaluru, Gurgaon/NCR. Target stage: Seed or Series A only. "
        "Respond with valid JSON only — no markdown, no explanation."
    )

    articles_text = "\n\n".join([
        f"Title: {n['title']}\nSummary: {n['summary']}\nURL: {n['url']}"
        for n in news_items[:20]
    ])

    rag_note = f"\nContext from previous runs:\n{rag_context}\n" if rag_context else ""

    user = f"""
{rag_note}
From these funding news articles, extract companies that are:
- Seed or Series A stage ONLY
- Located in Bengaluru or Gurgaon/NCR
- Tech startups (software, SaaS, fintech, healthtech, etc.)

For each qualifying company, score it 1-10 on how good it would be as a place to work for someone who wants:
- Smart, ambitious founders
- High learning curve
- High growth trajectory
- High impact early role
- Decent pay (12L+)

Return JSON array:
[{{
  "company": "name",
  "stage": "seed/series a",
  "domain": "fintech/saas/etc",
  "location": "Bengaluru/Gurgaon",
  "founders": "any founder info if mentioned",
  "why_interesting": "one sentence on why this is a good place to work",
  "score": 8,
  "source_url": "url"
}}]

If no qualifying companies found, return [].

Articles:
{articles_text}
"""
    try:
        raw    = call_claude(system, user)
        clean  = re.sub(r"```json|```", "", raw).strip()
        result = json.loads(clean)

        # Save to Companies sheet
        for co in result:
            safe_append_row(companies_sheet, [
                co.get("company", ""),
                co.get("stage", ""),
                co.get("domain", ""),
                co.get("location", ""),
                co.get("founders", ""),
                co.get("why_interesting", ""),
                co.get("source_url", ""),
                "",
                co.get("score", 0),
                time.ctime()
            ])

        high = [c for c in result if c.get("score", 0) >= 7]
        print(f"  🏢 Companies scored: {len(result)} | High quality: {len(high)}")
        return high

    except Exception as e:
        print(f"  ❌ Company scoring error: {e}")
        return []

# ==============================================================
# AI FILTER — score jobs via Claude with RAG context
# ==============================================================

def ai_filter_jobs(user_goal: str, jobs: list, rag_context: str) -> tuple:
    high, low = [], []
    batch_size = 20

    system = (
        "You are a job relevance scorer for a sharp 22-year-old BITS Pilani grad who wants to join "
        "a high-growth early-stage startup in Bengaluru or Gurgaon. "
        "He values: learning, impact, smart team, growth trajectory. Pay is secondary. "
        "Score jobs 1-10. Be strict — only score >= 7 if it's genuinely at a promising early-stage startup. "
        "Respond with valid JSON only."
    )

    for i in range(0, len(jobs), batch_size):
        batch = jobs[i: i + batch_size]
        lean  = [{"id": j["id"], "title": j["title"], "co": j["co"], "loc": j["loc"]} for j in batch]

        rag_note = f"Context from memory:\n{rag_context}\n\n" if rag_context else ""

        user = f"""
{rag_note}Goal: '{user_goal}'

Score each job 1-10 for how well it fits someone who wants to join a high-growth seed/series A startup 
in Bengaluru or Gurgaon for maximum learning and impact.

High scores (7+) only if:
- Company appears to be an early-stage startup (not a big corp, MNC, or agency)
- Role offers real ownership and learning
- Located in Bengaluru or Gurgaon/NCR

Return JSON array (no markdown):
[{{"id": "...", "score": 8, "reason": "One sentence why this fits."}}]
If nothing qualifies: []

Jobs:
{json.dumps(lean, indent=2)}
"""
        try:
            raw    = call_claude(system, user)
            clean  = re.sub(r"```json|```", "", raw).strip()
            scored = json.loads(clean)

            for m in scored:
                job = next((j for j in batch if j["id"] == m["id"]), None)
                if not job:
                    continue
                entry = {"job": job, "score": m.get("score", 0), "reason": m.get("reason", "")}
                if m.get("score", 0) >= CONFIDENCE_THRESHOLD:
                    high.append(entry)
                else:
                    low.append(entry)
        except Exception as e:
            print(f"  ❌ AI filter error: {e}")
            continue

    high.sort(key=lambda x: x["score"], reverse=True)
    capped   = high[:MAX_SLACK_PER_GOAL]
    overflow = high[MAX_SLACK_PER_GOAL:]
    return capped, low + overflow

# ==============================================================
# SLACK
# ==============================================================

SOURCE_EMOJI = {
    "greenhouse": "🌱", "lever": "⚙️", "linkedin": "💼",
    "indeed": "🔍", "google": "🔎", "naukri": "🇮🇳"
}

def send_companies_to_slack(companies: list):
    """Send high-quality newly funded companies to Slack."""
    if not companies:
        return

    lines = []
    for i, co in enumerate(companies[:8], 1):
        lines.append(
            f"*{i}. {co['company']}* — {co.get('domain','?')} · {co.get('stage','?').upper()}\n"
            f"   📍 {co.get('location','?')}  🎯 {co.get('score','?')}/10\n"
            f"   _{co.get('why_interesting','')}_\n"
            f"   <{co.get('source_url','#')}|→ Read more>"
        )

    payload = {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": f"🚀 {len(companies)} Freshly Funded Startups Worth Watching"}},
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n─────────────────────\n".join(lines)}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "_Source: Inc42 · Entrackr · YourStory · Google News_"}]}
        ]
    }
    try:
        r = requests.post(SLACK_WEBHOOK, json=payload, timeout=5)
        if r.status_code != 200:
            print(f"  ⚠️ Slack error: {r.status_code}")
    except Exception as e:
        print(f"  ⚠️ Slack error: {e}")


def send_digest_to_slack(user_goal: str, matches: list, total_scanned: int, silent_count: int):
    if not matches:
        return

    match_lines = []
    for i, m in enumerate(matches, 1):
        job   = m["job"]
        emoji = SOURCE_EMOJI.get(job.get("source", ""), "📌")
        line  = (
            f"*{i}. {job['title']}* — {job['co']}\n"
            f"   📍 {job['loc']}  {emoji} {job.get('source','?').capitalize()}  🎯 {m.get('score','?')}/10\n"
            f"   _{m['reason']}_\n"
            f"   <{job['url']}|→ Apply Now>"
        )
        match_lines.append(line)

    body_text = "\n─────────────────────\n".join(match_lines)
    footer    = (
        f"_{len(matches)} match{'es' if len(matches) != 1 else ''} "
        f"from {total_scanned} listings"
        + (f" · {silent_count} low-confidence logged to sheet" if silent_count else "")
        + "_"
    )

    payload = {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": f"🎯 {len(matches)} Job Match{'es' if len(matches)!=1 else ''} — {user_goal[:60]}"}},
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": body_text}},
            {"type": "divider"},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]}
        ]
    }
    try:
        r = requests.post(SLACK_WEBHOOK, json=payload, timeout=5)
        if r.status_code != 200:
            print(f"  ⚠️ Slack digest error: {r.status_code}")
    except Exception as e:
        print(f"  ⚠️ Slack error: {e}")


def send_summary_to_slack(results: list, duration_seconds: float, company_count: int):
    total = sum(c for _, c in results)
    lines = "\n".join(
        f"{'✅' if count > 0 else '⬜'} *{goal[:55]}* — {count} match{'es' if count!=1 else ''}"
        for goal, count in results
    )
    payload = {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": f"📋 Scout Run Complete — {total} job matches · {company_count} new companies"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": lines}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"⏱️ {duration_seconds:.0f}s · threshold:{CONFIDENCE_THRESHOLD}/10 · cap:{MAX_SLACK_PER_GOAL}/goal"}]}
        ]
    }
    try:
        requests.post(SLACK_SUMMARY_WEBHOOK, json=payload, timeout=5)
    except Exception as e:
        print(f"⚠️ Summary error: {e}")

# ==============================================================
# DISCOVERY
# ==============================================================

def discover_targets(user_goal: str) -> dict:
    print(f"  🕵️ Analyzing: '{user_goal}'...")
    system = (
        "You extract job search parameters from natural language. "
        "Respond with valid JSON only — no markdown, no explanation."
    )
    user = f"""
Extract search parameters from: '{user_goal}'

For someone looking at early-stage startups in Bengaluru/Gurgaon, use broad role terms.
If the goal is vague (e.g. 'any role at a startup'), use search_term = 'operations OR product OR growth OR engineering OR business' 

Return JSON:
{{"search_term": "...", "location": "Bengaluru", "greenhouse": [], "lever": []}}
"""
    try:
        raw    = call_claude(system, user)
        clean  = re.sub(r"```json|```", "", raw).strip()
        result = json.loads(clean)
        result.setdefault("search_term", "startup")
        result.setdefault("location", "Bengaluru")
        result.setdefault("greenhouse", [])
        result.setdefault("lever", [])
        return result
    except Exception as e:
        print(f"  ❌ Discovery error: {e}")
        return {"search_term": "startup operations product", "location": "Bengaluru", "greenhouse": [], "lever": []}

# ==============================================================
# MAIN SCOUT
# ==============================================================

def run_scout_parallel(user_goal: str, jobs_sheet, companies_sheet, existing_ids: set, existing_ids_lock, rag_context: str) -> tuple:
    tag = f"[{user_goal[:30]}]"
    print(f"\n{'='*50}\n🚀 {tag} Starting...")

    targets     = discover_targets(user_goal)
    search_term = targets["search_term"]
    location    = targets["location"]
    print(f"  📍 {tag} Term: '{search_term}' | Location: '{location}'")

    # Run both locations
    all_jobs = []
    for loc in ["Bengaluru", "Gurgaon"]:
        all_jobs.extend(fetch_jobs_jobspy(search_term, loc))

    ats_list = targets.get("greenhouse", []) + targets.get("lever", [])
    if ats_list:
        all_jobs.extend(fetch_jobs_ats(targets))

    with existing_ids_lock:
        new_jobs = [j for j in all_jobs if j["id"] not in existing_ids]

    print(f"  📊 {tag} Raw: {len(all_jobs)} | New: {len(new_jobs)}")
    if not new_jobs:
        print(f"  🏁 {tag} No new roles.")
        return user_goal, 0

    high_matches, low_matches = ai_filter_jobs(user_goal, new_jobs, rag_context)
    print(f"  🤖 {tag} Slack-worthy: {len(high_matches)} | Silent: {len(low_matches)}")

    all_matches = [(m, "yes") for m in high_matches] + [(m, "no") for m in low_matches]
    for m, slacked in all_matches:
        job = m["job"]
        safe_append_row(jobs_sheet, [
            job["id"], job["title"], job["co"], job["loc"],
            job.get("source", "?"), user_goal,
            m["score"], m["reason"], slacked, time.ctime(), ""
        ])
        with existing_ids_lock:
            existing_ids.add(job["id"])
        print(f"  {'✨' if slacked == 'yes' else '🔇'} {tag} {job['title']} @ {job['co']} [{m['score']}]")

    send_digest_to_slack(user_goal, high_matches, len(new_jobs), len(low_matches))
    return user_goal, len(high_matches)

# ==============================================================
# ENTRY POINT
# ==============================================================

if __name__ == "__main__":
    print(f"🤖 Job Scout v2 — {len(SEARCH_GOALS)} goal(s) | RAG enabled | Funding monitor ON")
    print(f"Goals: {SEARCH_GOALS}")

    jobs_sheet, companies_sheet = setup_sheets()
    existing_ids      = set(jobs_sheet.col_values(1))
    existing_ids_lock = threading.Lock()
    start_time        = time.time()

    # Step 1: Funding news monitor
    print("\n📰 Fetching funding news...")
    rag_context  = get_rag_context(companies_sheet, jobs_sheet)
    news_items   = fetch_funding_news()
    yc_companies = fetch_yc_companies()

    # Convert YC companies to news-like format for scoring
    for co in yc_companies:
        news_items.append({
            "title":   f"{co['name']} — YC {co['batch']}",
            "summary": f"{co['tagline']} | Location: {co['location']} | Stage: Seed (YC backed)",
            "url":     co["url"],
            "source":  "ycombinator",
            "date":    ""
        })

    hot_companies = score_companies_from_news(news_items, rag_context, companies_sheet)
    if hot_companies:
        send_companies_to_slack(hot_companies)
        print(f"  🔥 Sent {len(hot_companies)} hot companies to Slack")

    # Step 2: Job board scraping for each goal
    results = []
    if not SEARCH_GOALS:
        print("No goals set — skipping job scraping.")
    else:
        max_workers = min(len(SEARCH_GOALS), 3)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    run_scout_parallel, goal, jobs_sheet, companies_sheet,
                    existing_ids, existing_ids_lock, rag_context
                ): goal
                for goal in SEARCH_GOALS
            }
            for future in as_completed(futures):
                try:
                    goal, count = future.result()
                    results.append((goal, count))
                    print(f"✅ Done: '{goal}' → {count} matches")
                except Exception as e:
                    goal = futures[future]
                    print(f"❌ Failed: '{goal}' → {e}")
                    results.append((goal, 0))

    duration      = time.time() - start_time
    total         = sum(c for _, c in results)
    company_count = len(hot_companies)

    print(f"\n{'='*50}\n✅ All done in {duration:.0f}s | {total} job matches | {company_count} companies")
    send_summary_to_slack(results, duration, company_count)
