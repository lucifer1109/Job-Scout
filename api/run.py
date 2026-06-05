import requests, os, json, sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs
import io

RENDER_API_KEY    = os.environ.get("RENDER_API_KEY", "")
RENDER_SERVICE_ID = os.environ.get("RENDER_SERVICE_ID", "")

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not RENDER_API_KEY or not RENDER_SERVICE_ID:
            self._respond(500, {"error": f"Missing creds"})
            return

        # Read goals from request body
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body)
            goals = payload.get("goals", [])
        except:
            goals = []

        headers = {"Authorization": f"Bearer {RENDER_API_KEY}", "Content-Type": "application/json"}
        url = f"https://api.render.com/v1/cron-jobs/{RENDER_SERVICE_ID}/runs"

        body_payload = {}
        if goals:
            goals_str = "|".join(goals)
            body_payload = {"startCommand": f"SEARCH_GOALS=\"{goals_str}\" python scout.py"}

        print(f"[run] goals={goals} hitting {url}", file=sys.stderr)
        try:
            resp = requests.post(url, headers=headers, json=body_payload, timeout=10)
            print(f"[run] status={resp.status_code} body={resp.text[:300]}", file=sys.stderr)
            if resp.status_code in (200, 201):
                self._respond(200, {"status": "triggered"})
            else:
                self._respond(500, {"error": f"Render {resp.status_code}: {resp.text[:300]}"})
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
