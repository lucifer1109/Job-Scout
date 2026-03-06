import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import json
import time
import re
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai
from jobspy import scrape_jobs
import pandas as pd

# --- CONFIGURATION (from environment variables) ---
SLACK_WEBHOOK = os.environ["SLACK_WEBHOOK"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SHEET_NAME = os.environ.get("SHEET_NAME", "job scraper tool master sheet")

# Goals: read from env var, comma-separated for multiple
# e.g. SEARCH_GOALS="founders office in Bengaluru,chief of staff in Mumbai"
RAW_GOALS = os.environ.get("SEARCH_GOALS", "founders office roles in India")
SEARCH_GOALS = [g.strip() for g in RAW_GOALS.split(",") if g.strip()]

client = genai.Client(api_key=GEMINI_API_KEY)

# --- GOOGLE SHEETS AUTH ---
# Credentials are stored as an env var (JSON string) to avoid uploading files
def setup_sheets():
    creds_json = os.environ.get("GOOGLE_CREDS_JSON")
    if not creds_json:
        raise ValueError("GOOGLE_CREDS_JSON environment variable not set")

    creds_dict = json.loads(creds_json)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    gc = gspread.authorize(creds)
    return gc.open(SHEET_NAME).sheet1

# --- ATS FETCHER (Greenhouse + Lever) ---
def fetch_jobs_ats(targets):
    found_jobs = []
    for co in targets.get("greenhouse", []):
        try:
            r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{co}/jobs", timeout=10)
            if r.status_code == 200:
                for j in r.json().get('jobs', []):
                    found_jobs.append({
                        "id": f"gh_{j['id']}", "title": j['title'], "co": co,
                        "url": j['absolute_url'], "loc": j['location']['name'], "source": "greenhouse"
                    })
        except: continue
    for co in targets.get("lever", []):
        try:
            r = requests.get(f"https://api.lever.co/v0/postings/{co}", timeout=10)
            if r.status_code == 200:
                for j in r.json():
                    found_jobs.append({
                        "id": f"lv_{j['id']}", "title": j['text'], "co": co,
                        "url": j['hostedUrl'], "loc": j['categories'].get('location', 'Unknown'), "source": "lever"
                    })
        except: continue
    return found_jobs

# --- JOBSPY SCRAPER ---
def fetch_jobs_jobspy(search_term, location, results_per_site=25):
    found_jobs = []
    india_keywords = ["india", "bengaluru", "bangalore", "mumbai", "delhi",
                      "hyderabad", "pune", "chennai", "kolkata", "noida", "gurgaon", "gurugram"]
    is_india = any(kw in location.lower() for kw in india_keywords)

    sites = ["indeed", "linkedin", "google"]
    if is_india:
        sites.append("naukri")
        print(f"  🇮🇳 India detected — including Naukri")

    print(f"  🌐 Scraping {sites} for '{search_term}' in '{location}'...")
    try:
        jobs_df = scrape_jobs(
            site_name=sites,
            search_term=search_term,
            location=location,
            results_wanted=results_per_site,
            hours_old=72,
            country_indeed="india" if is_india else "usa",
        )
        if jobs_df is not None and not jobs_df.empty:
            for _, row in jobs_df.iterrows():
                raw_id = str(row.get('id', f"{row.get('title','')}_{row.get('company','')}"))
                job_id = f"jsp_{re.sub(r'[^a-zA-Z0-9]', '_', raw_id)[:40]}"
                found_jobs.append({
                    "id": job_id,
                    "title": str(row.get('title', 'Unknown')),
                    "co": str(row.get('company', 'Unknown')),
                    "url": str(row.get('job_url', '#')),
                    "loc": str(row.get('location', location)),
                    "source": str(row.get('site', 'jobspy')),
                })
            print(f"  ✅ JobSpy returned {len(found_jobs)} listings")
        else:
            print(f"  ⚠️ JobSpy returned 0 results")
    except Exception as e:
        print(f"  ❌ JobSpy error: {e}")
    return found_jobs

# --- SLACK ---
def send_to_slack(job, match_reason):
    source_emoji = {
        "greenhouse": "🌱", "lever": "⚙️", "linkedin": "💼",
        "indeed": "🔍", "google": "🔎", "naukri": "🇮🇳"
    }.get(job.get('source', ''), "📌")

    payload = {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "🎯 Scout Match Found"}},
            {"type": "section", "text": {"type": "mrkdwn", "text":
                f"*Role:* {job['title']}\n*Company:* {job['co'].upper()}\n"
                f"*Location:* {job['loc']}\n{source_emoji} *Source:* {job.get('source','?').capitalize()}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*AI Insight:*\n{match_reason}"}},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Apply Now"}, "url": job['url']}
            ]}
        ]
    }
    try:
        requests.post(SLACK_WEBHOOK, json=payload, timeout=5)
    except Exception as e:
        print(f"  ⚠️ Slack error: {e}")

# --- DISCOVERY ---
def discover_targets(user_goal):
    print(f"🕵️ Analyzing goal: '{user_goal}'...")
    prompt = f"""
    Analyze this job search goal: '{user_goal}'

    1. Extract:
       - "search_term": role/title keywords only (e.g. "founders office", "chief of staff")
       - "location": city or country only (e.g. "Bengaluru", "India")

    2. Identify companies using Greenhouse or Lever ATS:
       - "greenhouse": board slugs (ONLY if highly confident)
       - "lever": posting slugs (ONLY if highly confident)

    Most Indian startups do NOT use Greenhouse/Lever. Leave lists empty if unsure.

    Return ONLY this JSON:
    {{
        "search_term": "...",
        "location": "...",
        "greenhouse": [],
        "lever": []
    }}
    """
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            time.sleep(12)
            result = json.loads(response.text)
            result.setdefault("search_term", user_goal)
            result.setdefault("location", "India")
            result.setdefault("greenhouse", [])
            result.setdefault("lever", [])
            return result
        except Exception as e:
            if "429" in str(e):
                print(f"⏳ Rate limit. Waiting 30s...")
                time.sleep(30)
            else:
                raise e
    return {"search_term": user_goal, "location": "India", "greenhouse": [], "lever": []}

# --- MAIN SCOUT ---
def run_scout(user_goal, sheet, existing_ids):
    print(f"\n{'='*50}")
    print(f"🚀 Running scout for: '{user_goal}'")

    targets = discover_targets(user_goal)
    search_term = targets["search_term"]
    location = targets["location"]
    print(f"📍 Term: '{search_term}' | Location: '{location}'")

    all_jobs = fetch_jobs_jobspy(search_term, location)

    ats_targets = targets.get("greenhouse", []) + targets.get("lever", [])
    if ats_targets:
        print(f"🏗️ Also checking ATS: {ats_targets}")
        all_jobs.extend(fetch_jobs_ats(targets))

    new_jobs = [j for j in all_jobs if j['id'] not in existing_ids]
    print(f"📊 Raw: {len(all_jobs)} | New: {len(new_jobs)}")

    if not new_jobs:
        print("🏁 No new roles.")
        return 0

    total_matches = 0
    batch_size = 30

    for i in range(0, len(new_jobs), batch_size):
        batch = new_jobs[i: i + batch_size]
        lean = [{"id": j["id"], "title": j["title"], "co": j["co"], "loc": j["loc"]} for j in batch]
        prompt = f"""
        Goal: '{user_goal}'
        Return ONLY genuinely relevant job matches.
        Format: [{{"id": "...", "reason": "2-sentence explanation"}}] or []
        Jobs: {json.dumps(lean)}
        """
        success = False
        while not success:
            try:
                response = client.models.generate_content(
                    model='gemini-2.5-flash', contents=prompt,
                    config={'response_mime_type': 'application/json'}
                )
                matches = json.loads(re.sub(r'```json|```', '', response.text).strip())
                for m in matches:
                    job = next((j for j in batch if j['id'] == m['id']), None)
                    if job:
                        send_to_slack(job, m['reason'])
                        sheet.append_row([
                            job['id'], job['title'], job['co'], job['loc'],
                            job.get('source', '?'), m['reason'], time.ctime()
                        ])
                        # Track so later goals in same run don't re-alert
                        existing_ids.add(job['id'])
                        print(f"  ✨ {job['title']} @ {job['co']} [{job.get('source','?')}]")
                        total_matches += 1
                success = True
                time.sleep(12)
            except Exception as e:
                if "429" in str(e):
                    print("⏳ Rate limit. Sleeping 45s...")
                    time.sleep(45)
                else:
                    print(f"❌ Batch error: {e}")
                    break

    return total_matches

# --- THREAD-SAFE SHEET WRITER ---
# gspread is not thread-safe — all writes go through a single lock
sheet_lock = threading.Lock()

def safe_append_row(sheet, row):
    with sheet_lock:
        sheet.append_row(row)

# --- PARALLEL-SAFE SCOUT WRAPPER ---
def run_scout_parallel(user_goal, sheet, existing_ids, existing_ids_lock):
    """
    Thread-safe version of run_scout.
    Uses locks for shared state (existing_ids set + sheet writes).
    """
    print(f"\n{'='*50}")
    print(f"🚀 [{user_goal}] Starting...")

    targets = discover_targets(user_goal)
    search_term = targets["search_term"]
    location = targets["location"]
    print(f"📍 [{user_goal[:30]}] Term: '{search_term}' | Location: '{location}'")

    all_jobs = fetch_jobs_jobspy(search_term, location)

    ats_targets = targets.get("greenhouse", []) + targets.get("lever", [])
    if ats_targets:
        print(f"🏗️ [{user_goal[:30]}] Checking ATS: {ats_targets}")
        all_jobs.extend(fetch_jobs_ats(targets))

    # Thread-safe read of existing_ids
    with existing_ids_lock:
        new_jobs = [j for j in all_jobs if j['id'] not in existing_ids]

    print(f"📊 [{user_goal[:30]}] Raw: {len(all_jobs)} | New: {len(new_jobs)}")

    if not new_jobs:
        print(f"🏁 [{user_goal[:30]}] No new roles.")
        return user_goal, 0

    total_matches = 0
    batch_size = 30

    for i in range(0, len(new_jobs), batch_size):
        batch = new_jobs[i: i + batch_size]
        lean = [{"id": j["id"], "title": j["title"], "co": j["co"], "loc": j["loc"]} for j in batch]
        prompt = f"""
        Goal: '{user_goal}'
        Return ONLY genuinely relevant job matches.
        Format: [{{"id": "...", "reason": "2-sentence explanation"}}] or []
        Jobs: {json.dumps(lean)}
        """
        success = False
        while not success:
            try:
                response = client.models.generate_content(
                    model='gemini-2.5-flash', contents=prompt,
                    config={'response_mime_type': 'application/json'}
                )
                matches = json.loads(re.sub(r'```json|```', '', response.text).strip())

                for m in matches:
                    job = next((j for j in batch if j['id'] == m['id']), None)
                    if job:
                        send_to_slack(job, m['reason'])
                        safe_append_row(sheet, [
                            job['id'], job['title'], job['co'], job['loc'],
                            job.get('source', '?'), user_goal, m['reason'], time.ctime()
                        ])
                        # Thread-safe update of seen IDs
                        with existing_ids_lock:
                            existing_ids.add(job['id'])
                        print(f"  ✨ [{user_goal[:25]}] {job['title']} @ {job['co']}")
                        total_matches += 1

                success = True
                time.sleep(12)  # Gemini rate limit cooldown per thread

            except Exception as e:
                if "429" in str(e):
                    print(f"  ⏳ [{user_goal[:25]}] Rate limit. Sleeping 45s...")
                    time.sleep(45)
                else:
                    print(f"  ❌ [{user_goal[:25]}] Batch error: {e}")
                    break

    return user_goal, total_matches

# --- SUMMARY SLACK MESSAGE ---
def send_summary_to_slack(results, duration_seconds):
    total = sum(count for _, count in results)
    lines = "\n".join(
        f"{'✅' if count > 0 else '⬜'} *{goal[:50]}* — {count} match{'es' if count != 1 else ''}"
        for goal, count in results
    )
    payload = {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": f"📋 Scout Run Complete — {total} total matches"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": lines}},
            {"type": "context", "elements": [
                {"type": "mrkdwn", "text": f"⏱️ Completed in {duration_seconds:.0f}s | {len(results)} goals ran in parallel"}
            ]}
        ]
    }
    try:
        requests.post(SLACK_WEBHOOK, json=payload, timeout=5)
    except Exception as e:
        print(f"⚠️ Summary Slack error: {e}")

# --- ENTRY POINT ---
if __name__ == "__main__":
    print(f"🤖 Job Scout starting — {len(SEARCH_GOALS)} goal(s) running in PARALLEL")
    print(f"Goals: {SEARCH_GOALS}")

    # TEMPORARY DEBUG — remove after fixing
    print("=== ENV VARS CHECK ===")
    print(f"GEMINI_API_KEY set: {bool(os.environ.get('GEMINI_API_KEY'))}")
    print(f"SLACK_WEBHOOK set: {bool(os.environ.get('SLACK_WEBHOOK'))}")
    print(f"GOOGLE_CREDS_JSON set: {bool(os.environ.get('GOOGLE_CREDS_JSON'))}")
    print(f"SHEET_NAME: {os.environ.get('SHEET_NAME')}")
    print(f"SEARCH_GOALS: {os.environ.get('SEARCH_GOALS')}")
    print("=== END CHECK ===")

    sheet = setup_sheets()
    existing_ids = set(sheet.col_values(1))
    existing_ids_lock = threading.Lock()

    start_time = time.time()
    results = []

    # Run all goals simultaneously — cap at 5 workers to avoid rate limits
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
                print(f"✅ Finished: '{goal}' → {count} matches")
            except Exception as e:
                goal = futures[future]
                print(f"❌ Failed: '{goal}' → {e}")
                results.append((goal, 0))

    duration = time.time() - start_time
    total = sum(c for _, c in results)

    print(f"\n{'='*50}")
    print(f"✅ All goals done in {duration:.0f}s. Total matches: {total}")
    for goal, count in results:
        print(f"  {'✅' if count > 0 else '⬜'} {goal} → {count} matches")

    # Send one summary message to Slack instead of just per-match pings
    send_summary_to_slack(results, duration)
