"""Run the existing SLP web UI locally with no paid hosting or storage.

Requirements (install only once):
  python -m pip install -r requirements.txt
  python -m pip install fastapi uvicorn python-multipart cryptography xlrd

The user's PC must remain on for the local site to be reachable. Google Apps
Script continues its own hourly mail archiving while the PC is off.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home() / "SLP_Free"
SETTINGS = HOME / "local_secrets.json"


def setup() -> dict:
    if SETTINGS.is_file():
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    print("SLP local: first-run setup, without new cloud billing.")
    url = input("Apps Script Web App /exec URL: ").strip()
    key = getpass.getpass("SLP_BRIDGE_KEY (shared with Apps Script): ").strip()
    if not url.startswith("https://script.google.com/macros/s/") or not url.endswith("/exec"):
        raise SystemExit("Invalid Apps Script URL")
    if len(key) < 48:
        raise SystemExit("Shared secret must be at least 48 characters")
    password = getpass.getpass("New local SLP admin password: ")
    confirmation = getpass.getpass("Repeat password: ")
    if password != confirmation or len(password) < 12:
        raise SystemExit("Passwords do not match or have fewer than 12 characters")
    salt = secrets.token_bytes(24)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32,
    )
    encoded = (
        "scrypt$" + base64.b64encode(salt).decode() +
        "$" + base64.b64encode(digest).decode()
    )
    configuration = {
        "url": url, "key": key,
        "session_secret": secrets.token_urlsafe(48),
        "users": {"Skoomaholic": {"role": "admin", "password_hash": encoded}},
    }
    temp = SETTINGS.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as target:
        json.dump(configuration, target, ensure_ascii=False)
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass  # Windows: protect the home folder with your account permissions.
    temp.replace(SETTINGS)
    return configuration


if __name__ == "__main__":
    configuration = setup()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    os.environ.update({
        "SPORT_FREE_SCRIPT_URL": configuration["url"],
        "SPORT_FREE_SCRIPT_KEY": configuration["key"],
        "SPORT_WEB_SECRET": configuration["session_secret"],
        "SPORT_WEB_USERS": json.dumps(configuration["users"]),
        "SPORT_PUBLIC_URL": "http://127.0.0.1:8080",
        "SLP_DB_PATH": str(HOME / "slp.db"),
        "SPORT_TEMPLATE_PATH": str(HOME / "approved_template.xlsx"),
        "SPORT_GMAIL_ENABLE_TEST_SEND": "false",
        "SPORT_GMAIL_AUTO_IMPORT": "true",
    })
    if os.environ.get("SPORT_GCS_BUCKET"):
        raise SystemExit("Remove SPORT_GCS_BUCKET for strictly free local mode")
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "Install requirements and uvicorn first (instructions at top of file)"
        ) from exc
    print("Open http://127.0.0.1:8080 in your browser.")
    uvicorn.run("cloudrun_web:app", host="127.0.0.1", port=8080, workers=1)
