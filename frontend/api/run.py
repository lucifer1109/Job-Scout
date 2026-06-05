import requests, os, json
from http.server import BaseHTTPRequestHandler

RENDER_API_KEY    = os.environ.get("RENDER_API_KEY", "")
RENDER_SERVICE_ID = os.environ.get("RENDER_SERVICE_ID", "")

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not RENDER_API_KEY or not RENDER_SERVICE_ID:
            self._respond(500, {"error": "Render credentials not configured"})
            return
        headers = {"Authorization": f"Bearer {RENDER_API_KEY}", "Content-Type": "application/json"}
        url = f"https://api.render.com/v1/cronjobs/{RENDER_SERVICE_ID}/trigger"
        try:
            resp = requests.post(url, headers=headers, json={}, timeout=10)
            if resp.status_code in (200, 201):
                self._respond(200, {"status": "triggered"})
            else:
                self._respond(500, {"error": f"Render API error {resp.status_code}: {resp.text}"})
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
