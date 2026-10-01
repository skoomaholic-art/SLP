"""Create a private Cloud Run user-password-hash JSON file; no passwords in Git.

Usage: python scripts/cloudrun_users.py /tmp/sport-users.json
Then upload the JSON as a Secret Manager secret SPORT_WEB_USERS.
Do not paste its contents into chat or Git.
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

ACCOUNTS = (
    ("Skoomaholic", "admin", "S"),
    ("Дания", "editor", "🇩🇰"),
    ("Вадим", "editor", "🐈‍⬛"),
    ("USER1", "editor", "U"),
    ("USER2", "editor", "U"),
    ("USER3", "editor", "U"),
    ("USER4", "editor", "U"),
)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/cloudrun_users.py /private/path/users.json")
    target = Path(sys.argv[1]).expanduser()
    if target.exists():
        raise SystemExit("File already exists: refusing overwrite")
    target.parent.mkdir(parents=True, exist_ok=True)
    users = {}
    for username, role, avatar in ACCOUNTS:
        while True:
            password = getpass.getpass(f"New password for {username} (at least 12 chars): ")
            if len(password) >= 12:
                break
            print("Password is too short, choose a new one.", file=sys.stderr)
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
        users[username] = {
            "role": role, "avatar": avatar,
            "password_hash": "scrypt$" + base64.b64encode(salt).decode()
            + "$" + base64.b64encode(digest).decode(),
        }
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False)
    print("Created protected hash file:", target)
    print("Store as a secret; remove the local copy when finished.")


if __name__ == "__main__":
    main()
