from __future__ import annotations

import asyncio
try:
    from agent_reach.channels.web import WebChannel
except Exception as exc:  # pragma: no cover - surfaced by runtime helper
    WebChannel = None
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None


def agent_reach_available() -> bool:
    """Return whether the Agent Reach web capability can be imported."""
    return WebChannel is not None


def read_public_url_sync(url: str) -> str:
    """Read a public HTTP(S) page through Agent Reach/Jina Reader.

    Agent Reach performs public-URL validation and rejects local/private
    destinations before handing the request to Jina Reader.
    """
    if WebChannel is None:
        detail = f": {_IMPORT_ERROR}" if _IMPORT_ERROR else ""
        raise RuntimeError(f"Agent Reach is not available{detail}")

    text = WebChannel().read(url)
    if not str(text or "").strip():
        raise RuntimeError("Agent Reach returned an empty page")
    return str(text)


async def read_public_url(url: str, *, timeout_seconds: float = 35.0) -> str:
    """Async wrapper for Agent Reach's synchronous web reader."""
    return await asyncio.wait_for(
        asyncio.to_thread(read_public_url_sync, url),
        timeout=timeout_seconds,
    )
