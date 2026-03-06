"""
Google Colab Helper — Base64-encode a service account JSON file
and output a ready-to-run Railway CLI command.

Usage (in a Colab cell):
    !python encode_creds.py
"""

import base64, json, pathlib, sys

CREDS_PATH = "/content/colab json key.json"

# --- Read & validate the JSON ---
path = pathlib.Path(CREDS_PATH)
if not path.exists():
    sys.exit(f"❌ File not found: {CREDS_PATH}")

raw = path.read_text()
try:
    json.loads(raw)  # validate it's real JSON
except json.JSONDecodeError as e:
    sys.exit(f"❌ Invalid JSON: {e}")

# --- Base64-encode (no newlines) ---
encoded = base64.b64encode(raw.encode()).decode()

print("✅ Encoded successfully!\n")
print("=" * 60)
print("Copy-paste this command into your Colab terminal:\n")
print(f'railway variables set GOOGLE_CREDS_JSON="{encoded}"')
print("=" * 60)
print(f"\n📏 Encoded length: {len(encoded)} characters")
