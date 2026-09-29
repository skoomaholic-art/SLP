"""Send only SLP editorial-attention markers to the existing personal assistant.

The bridge is opt-in. It never transfers message bodies, sender addresses,
attachment names or workbook bytes. Gmail OAuth and imports remain owned by SLP.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from services.time_logic import KZ_TIMEZONE


def deliver_pending(database) -> dict:
    url = os.getenv("SPORT_ASSISTANT_NOTICE_URL", "").strip()
    secret = os.getenv("SPORT_ASSISTANT_NOTICE_SECRET", "").strip()
    if not url or not secret:
        return {"enabled": False, "delivered": 0, "failed": 0}
    target = urlsplit(url)
    if (target.scheme != "https" or not target.hostname or
            target.username or target.password or target.query or
            target.fragment or target.path != "/internal/slp/notice"):
        return {"enabled": False, "delivered": 0, "failed": 1}

    # Additive local outbox; repeated syncs and restarts cannot lose the
    # deduplication key or mark a notice delivered before the receiver acks.
    with database._connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS assistant_notice_outbox (
                source_key TEXT PRIMARY KEY,
                delivered_at TEXT NOT NULL
            )
        """)
        rows = conn.execute("""
            SELECT n.message_id,n.attachment_id,n.detected_channel,n.status
              FROM gmail_notices AS n
             WHERE n.status IN ('pending','review')
               AND NOT EXISTS (
                 SELECT 1 FROM assistant_notice_outbox AS d
                  WHERE d.source_key=n.message_id || ':' || n.attachment_id
               )
             ORDER BY n.id ASC LIMIT 8
        """).fetchall()

    delivered = failed = 0
    for row in rows:
        key = str(row["message_id"]) + ":" + str(row["attachment_id"])
        body = json.dumps({
            "id": sha256(key.encode("utf-8")).hexdigest()[:32],
            "status": str(row["status"]),
            "channel": str(row["detected_channel"] or "")[:120],
        }, ensure_ascii=False).encode("utf-8")
        request = Request(
            url, data=body, method="POST",
            headers={"Authorization": "Bearer " + secret,
                     "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=3) as response:
                if response.status != 200:
                    failed += 1
                    continue
                result = json.loads(response.read(2000))
            if result.get("ok") is not True or result.get("notification") != "queued":
                failed += 1
                continue
        except (HTTPError, URLError, TimeoutError, ValueError, OSError):
            failed += 1
            continue
        with database._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO assistant_notice_outbox "
                "(source_key,delivered_at) VALUES(?,?)",
                (key, datetime.now(KZ_TIMEZONE).isoformat()),
            )
        delivered += 1
    return {"enabled": True, "delivered": delivered, "failed": failed}
