import requests, os, json, sys
from http.server import BaseHTTPRequestHandler

RENDER_API_KEY    = os.environ.get("RENDER_API_KEY", "")
RENDER_SERVICE_ID = os.environ.get("RENDER_SERVICE_ID", "")

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not RENDER_API_KEY or not RENDER_SERVICE_ID:
            self._respond(500, {"error": "Missing creds"})
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body)
            goals = payload.get("goals", [])
        except:
            goals = []

        hdrs = {"Authorization": f"Bearer {RENDER_API_KEY}", "Content-Type": "application/json"}

        # Step 1: update only SEARCH_GOALS, preserving all other env vars
        if goals:
            goals_str = ",".join(goals)
            # First fetch existing env vars
            env_url = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars"
            existing_resp = requests.get(env_url, headers=hdrs, timeout=10)
            if existing_resp.status_code == 200:
                existing = existing_resp.json()
                # Update or add SEARCH_GOALS
                found = False
                for var in existing:
                    if var.get("envVar", {}).get("key") == "SEARCH_GOALS":
                        var["envVar"]["value"] = goals_str
                        found = True
                        break
                if not found:
                    existing.append({"envVar": {"key": "SEARCH_GOALS", "value": goals_str}})
                # PUT back the full list
                put_body = [{"key": v["envVar"]["key"], "value": v["envVar"]["value"]} for v in existing]
                update_resp = requests.put(env_url, headers=hdrs, json=put_body, timeout=10)
                print(f"[run] env update={update_resp.status_code}", file=sys.stderr)

        # Step 2: trigger the cron job
        trigger_url = f"https://api.render.com/v1/cron-jobs/{RENDER_SERVICE_ID}/runs"
        try:
            resp = requests.post(trigger_url, headers=hdrs, json={}, timeout=10)
            print(f"[run] trigger={resp.status_code} {resp.text[:100]}", file=sys.stderr)
            if resp.status_code in (200, 201):
                self._respond(200, {"status": "triggered"})
            else:
                self._respond(500, {"error": f"Render {resp.status_code}: {resp.text[:200]}"})
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_OPTIONS(self):
        self._respond(200, {})

    def _respond(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *args): pass
