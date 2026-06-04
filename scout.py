import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import json
import time
import re
import os
import base64
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import anthropic
from jobspy import scrape_jobs
import pandas as pd

# ==============================================================
# CONFIGURATION
# All values come from environment variables — never hardcoded.
# See .env.example for the full list.
# ==============================================================

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SHEET_NAME        = os.environ.get("SHEET_NAME", "job scraper tool master sheet")
SHEET_ID          = os.environ.get("SHEET_ID", "")   # optional: target by ID instead of name

# --- Slack webhooks ---
SLACK_WEBHOOK         = os.environ["SLACK_WEBHOOK"]
SLACK_SUMMARY_WEBHOOK = os.environ.get("SLACK_SUMMARY_WEBHOOK", SLACK_WEBHOOK)

# GOAL_CHANNELS format: "goal keyword:webhook_url|goal keyword2:webhook_url2"
_raw_goal_channels = os.environ.get("GOAL_CHANNELS", "")
GOAL_CHANNEL_MAP: dict = {}
if _raw_goal_channels:
    for pair in _raw_goal_channels.split("|"):
        if ":" in pair:
            key, url = pair.split(":", 1)
            GOAL_CHANNEL_MAP[key.strip().lower()] = url.strip()

# --- Search goals ---
RAW_GOALS    = os.environ.get("SEARCH_GOALS", "founders office roles in India")
SEARCH_GOALS = [g.strip() for g in RAW_GOALS.split(",") if g.strip()]

# --- Tuning knobs ---
CONFIDENCE_THRESHOLD = int(os.environ.get("CONFIDENCE_THRESHOLD", "7"))
MAX_SLACK_PER_GOAL   = int(os.environ.get("MAX_SLACK_PER_GOAL", "10"))
RESULTS_PER_SITE     = int(os.environ.get("RESULTS_PER_SITE", "25"))
HOURS_OLD            = int(os.environ.get("HOURS_OLD", "72"))

# Anthropic client (shared across threads — thread-safe)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ==============================================================
# GOOGLE SHEETS AUTH
# ==============================================================

def setup_sheets():
    creds_raw = os.environ.get("GOOGLE_CREDS_JSON")
    if not creds_raw:
        raise ValueError("GOOGLE_CREDS_JSON environment variable not set")
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

    # Open by ID (preferred — survives sheet renames) or fall back to name
    if SHEET_ID:
        sh = gc.open_by_key(SHEET_ID)
    else:
        sh = gc.open(SHEET_NAME)

    worksheet = sh.sheet1

    # Write header row if the sheet is empty
    if not worksheet.row_values(1):
        worksheet.append_row([
            "Job ID", "Title", "Company", "Location",
            "Source", "Goal", "Score", "Reason", "Slacked?", "Timestamp"
        ])

    return worksheet

sheet_lock = threading.Lock()

def safe_append_row(sheet, row):
    with sheet_lock:
        sheet.append_row(row)

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
        except: continue
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
        except: continue
    return found_jobs


def fetch_jobs_jobspy(search_term, location):
    found_jobs = []
    india_keywords = [
        "india", "bengaluru", "bangalore", "mumbai", "delhi", "hyderabad",
        "pune", "chennai", "kolkata", "noida", "gurgaon", "gurugram"
    ]
    is_india = any(kw in location.lower() for kw in india_keywords)
    sites = ["indeed", "linkedin", "google"]
    if is_india:
        sites.append("naukri")
        print(f"  🇮🇳 India detected — adding Naukri")
    print(f"  🌐 Scraping {sites} for '{search_term}' in '{location}'...")
    try:
        jobs_df = scrape_jobs(
            site_name=sites,
            search_term=search_term,
            location=location,
            results_wanted=RESULTS_PER_SITE,
            hours_old=HOURS_OLD,
            country_indeed="india" if is_india else "usa",
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
# SLACK — DIGEST FORMAT
# ==============================================================

SOURCE_EMOJI = {
    "greenhouse": "🌱", "lever": "⚙️", "linkedin": "💼",
    "indeed": "🔍", "google": "🔎", "naukri": "🇮🇳"
}

def get_webhook_for_goal(user_goal: str) -> str:
    goal_lower = user_goal.lower()
    for key, webhook in GOAL_CHANNEL_MAP.items():
        if key in goal_lower:
            return webhook
    return SLACK_WEBHOOK


def send_digest_to_slack(user_goal: str, matches: list, total_scanned: int, silent_count: int):
    if not matches:
        return

    webhook     = get_webhook_for_goal(user_goal)
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
        f"_{len(matches)} high-confidence match{'es' if len(matches) != 1 else ''} "
        f"from {total_scanned} new listings"
        + (f" · {silent_count} low-confidence silently logged to sheet" if silent_count else "")
        + "_"
    )

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🎯 {len(matches)} Match{'es' if len(matches)!=1 else ''} — {user_goal[:60]}"
                }
            },
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": body_text}},
            {"type": "divider"},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]}
        ]
    }
    try:
        r = requests.post(webhook, json=payload, timeout=5)
        if r.status_code != 200:
            print(f"  ⚠️ Slack digest error: {r.status_code} {r.text}")
    except Exception as e:
        print(f"  ⚠️ Slack error: {e}")


def send_summary_to_slack(results: list, duration_seconds: float):
    total = sum(c for _, c in results)
    lines = "\n".join(
        f"{'✅' if count > 0 else '⬜'} *{goal[:55]}* — {count} match{'es' if count!=1 else ''}"
        for goal, count in results
    )
    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"📋 Scout Run Complete — {total} total matches"}
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": lines}},
            {
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": (
                        f"⏱️ {duration_seconds:.0f}s  |  {len(results)} goals in parallel  |  "
                        f"Threshold: {CONFIDENCE_THRESHOLD}/10  |  Cap: top {MAX_SLACK_PER_GOAL} per goal"
                    )
                }]
            }
        ]
    }
    try:
        requests.post(SLACK_SUMMARY_WEBHOOK, json=payload, timeout=5)
    except Exception as e:
        print(f"⚠️ Summary Slack error: {e}")

# ==============================================================
# CLAUDE — JSON helper
# Wraps all Claude API calls with retry on overload
# ==============================================================

def call_claude(system_prompt: str, user_prompt: str, retries: int = 3) -> str:
    """Call Claude and return the text response. Retries on 529 overload."""
    for attempt in range(retries):
        try:
            message = claude.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            return message.content[0].text
        except anthropic.RateLimitError:
            wait = 30 * (attempt + 1)
            print(f"  ⏳ Claude rate limit. Waiting {wait}s...")
            time.sleep(wait)
        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                wait = 20 * (attempt + 1)
                print(f"  ⏳ Claude overloaded. Waiting {wait}s...")
                time.sleep(wait)
            else:
                raise e
    raise RuntimeError("Claude API failed after all retries")


# ==============================================================
# DISCOVERY
# ==============================================================

def discover_targets(user_goal: str) -> dict:
    print(f"  🕵️ Analyzing: '{user_goal}'...")
    system = (
        "You are a job search analyst. You extract structured search parameters from natural language goals. "
        "Always respond with valid JSON only — no markdown, no explanation, no code fences."
    )
    user = f"""
Analyze this job search goal: '{user_goal}'

Return JSON with:
1. "search_term": role/title keywords only (e.g. "founders office", "chief of staff")
2. "location": city or country only (e.g. "Bengaluru", "India")
3. "greenhouse": Greenhouse board slugs — ONLY if highly confident
4. "lever": Lever posting slugs — ONLY if highly confident

Most Indian startups do NOT use Greenhouse or Lever. Leave both as empty lists if unsure.

Return ONLY valid JSON (no markdown, no backticks):
{{"search_term": "...", "location": "...", "greenhouse": [], "lever": []}}
"""
    try:
        raw = call_claude(system, user)
        # Strip any accidental markdown fences
        clean = re.sub(r"```json|```", "", raw).strip()
        result = json.loads(clean)
        result.setdefault("search_term", user_goal)
        result.setdefault("location", "India")
        result.setdefault("greenhouse", [])
        result.setdefault("lever", [])
        return result
    except Exception as e:
        print(f"  ❌ Discovery error: {e}")
        return {"search_term": user_goal, "location": "India", "greenhouse": [], "lever": []}


# ==============================================================
# AI FILTER — confidence scoring via Claude
# ==============================================================

def ai_filter_jobs(user_goal: str, jobs: list) -> tuple:
    """
    Returns (high_confidence, low_confidence).
    High = score >= CONFIDENCE_THRESHOLD → Slack digest.
    Low  = score <  CONFIDENCE_THRESHOLD → sheet only, silent.
    """
    high, low = [], []
    batch_size = 30

    system = (
        "You are a job relevance scorer. Given a job search goal and a list of job listings, "
        "you score each listing for relevance on a scale of 1-10. "
        "Always respond with a valid JSON array only — no markdown, no explanation, no code fences."
    )

    for i in range(0, len(jobs), batch_size):
        batch = jobs[i: i + batch_size]
        lean  = [{"id": j["id"], "title": j["title"], "co": j["co"], "loc": j["loc"]} for j in batch]

        user = f"""
Goal: '{user_goal}'

Score each job for relevance on a scale of 1-10.
Only include jobs with any relevance (score >= 4). Skip completely irrelevant jobs.

Return a JSON array (no markdown, no backticks):
[{{"id": "...", "score": 8, "reason": "One sentence why this matches."}}]
If nothing is relevant, return: []

Jobs to score:
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

    # Sort by score, apply per-goal cap
    high.sort(key=lambda x: x["score"], reverse=True)
    capped   = high[:MAX_SLACK_PER_GOAL]
    overflow = high[MAX_SLACK_PER_GOAL:]
    return capped, low + overflow


# ==============================================================
# MAIN SCOUT — one thread per goal
# ==============================================================

def run_scout_parallel(user_goal: str, sheet, existing_ids: set, existing_ids_lock) -> tuple:
    tag = f"[{user_goal[:30]}]"
    print(f"\n{'='*50}\n🚀 {tag} Starting...")

    targets     = discover_targets(user_goal)
    search_term = targets["search_term"]
    location    = targets["location"]
    print(f"  📍 {tag} Term: '{search_term}' | Location: '{location}'")

    all_jobs = fetch_jobs_jobspy(search_term, location)
    ats_list = targets.get("greenhouse", []) + targets.get("lever", [])
    if ats_list:
        print(f"  🏗️ {tag} Also checking ATS: {ats_list}")
        all_jobs.extend(fetch_jobs_ats(targets))

    with existing_ids_lock:
        new_jobs = [j for j in all_jobs if j["id"] not in existing_ids]

    print(f"  📊 {tag} Raw: {len(all_jobs)} | New: {len(new_jobs)}")
    if not new_jobs:
        print(f"  🏁 {tag} No new roles.")
        return user_goal, 0

    high_matches, low_matches = ai_filter_jobs(user_goal, new_jobs)
    print(
        f"  🤖 {tag} Slack-worthy: {len(high_matches)} | "
        f"Silent: {len(low_matches)} | "
        f"No match: {len(new_jobs) - len(high_matches) - len(low_matches)}"
    )

    # Write everything to sheet (high + low confidence)
    all_matches = [(m, "yes") for m in high_matches] + [(m, "no — low confidence") for m in low_matches]
    for m, slacked in all_matches:
        job = m["job"]
        safe_append_row(sheet, [
            job["id"], job["title"], job["co"], job["loc"],
            job.get("source", "?"), user_goal,
            m["score"], m["reason"], slacked, time.ctime()
        ])
        with existing_ids_lock:
            existing_ids.add(job["id"])
        flag = "✨" if slacked == "yes" else "🔇"
        print(f"  {flag} {tag} {job['title']} @ {job['co']} [score:{m['score']}]")

    # Send ONE digest to Slack
    send_digest_to_slack(
        user_goal, high_matches,
        total_scanned=len(new_jobs),
        silent_count=len(low_matches)
    )

    return user_goal, len(high_matches)


# ==============================================================
# ENTRY POINT
# ==============================================================

if __name__ == "__main__":
    print(
        f"🤖 Job Scout — {len(SEARCH_GOALS)} goal(s) | "
        f"parallel | threshold:{CONFIDENCE_THRESHOLD}/10 | cap:{MAX_SLACK_PER_GOAL}/goal"
    )
    print(f"Goals: {SEARCH_GOALS}")

    sheet             = setup_sheets()
    existing_ids      = set(sheet.col_values(1))
    existing_ids_lock = threading.Lock()
    start_time        = time.time()
    results           = []

    max_workers = min(len(SEARCH_GOALS), 5)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(run_scout_parallel, goal, sheet, existing_ids, existing_ids_lock): goal
            for goal in SEARCH_GOALS
        }
        for future in as_completed(futures):
            try:
                goal, count = future.result()
                results.append((goal, count))
                print(f"✅ Done: '{goal}' → {count} Slack matches")
            except Exception as e:
                goal = futures[future]
                print(f"❌ Failed: '{goal}' → {e}")
                results.append((goal, 0))

    duration = time.time() - start_time
    total    = sum(c for _, c in results)
    print(f"\n{'='*50}\n✅ All done in {duration:.0f}s | {total} total Slack matches")
    for goal, count in results:
        print(f"  {'✅' if count > 0 else '⬜'} {goal} → {count}")

    send_summary_to_slack(results, duration)
