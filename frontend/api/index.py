from flask import Flask, jsonify, request
from flask_cors import CORS
import requests, os, json

app = Flask(__name__)
CORS(app)

RENDER_API_KEY    = os.environ.get("RENDER_API_KEY", "")
RENDER_SERVICE_ID = os.environ.get("RENDER_SERVICE_ID", "")
GOALS_FILE        = os.environ.get("GOALS_FILE", "goals.json")

def load_goals():
    if os.path.exists(GOALS_FILE):
        with open(GOALS_FILE) as f:
            return json.load(f)
    return []

def save_goals(goals):
    with open(GOALS_FILE, "w") as f:
        json.dump(goals, f)

@app.route("/api/goals", methods=["GET"])
def get_goals():
    return jsonify(load_goals())

@app.route("/api/goals", methods=["POST"])
def add_goal():
    data = request.json
    goal = data.get("goal", "").strip()
    if not goal:
        return jsonify({"error": "Goal cannot be empty"}), 400
    goals = load_goals()
    if goal not in goals:
        goals.append(goal)
        save_goals(goals)
    return jsonify(goals)

@app.route("/api/goals/<int:index>", methods=["DELETE"])
def delete_goal(index):
    goals = load_goals()
    if 0 <= index < len(goals):
        goals.pop(index)
        save_goals(goals)
    return jsonify(goals)

@app.route("/api/run", methods=["POST"])
def trigger_run():
    if not RENDER_API_KEY or not RENDER_SERVICE_ID:
        return jsonify({"error": "Render credentials not configured"}), 500
    headers = {"Authorization": f"Bearer {RENDER_API_KEY}", "Content-Type": "application/json"}
    url = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/jobs"
    resp = requests.post(url, headers=headers, json={}, timeout=10)
    if resp.status_code in (200, 201):
        return jsonify({"status": "triggered", "message": "Run started on Render"})
    return jsonify({"error": f"Render API error: {resp.status_code} {resp.text}"}), 500

@app.route("/api/logs", methods=["GET"])
def get_logs():
    if not RENDER_API_KEY or not RENDER_SERVICE_ID:
        return jsonify({"error": "Render credentials not configured"}), 500
    headers = {"Authorization": f"Bearer {RENDER_API_KEY}"}
    url = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/deploys?limit=5"
    resp = requests.get(url, headers=headers, timeout=10)
    if resp.status_code == 200:
        return jsonify(resp.json())
    return jsonify({"error": f"Render API error: {resp.status_code}"}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5001)
