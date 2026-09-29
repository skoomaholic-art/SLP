"""Optional Chromium fallback for JavaScript-rendered television guides.

Only called after the lightweight HTTP parser fails. The caller still applies
its existing LIVE evidence rules and last-good snapshot protections.
"""
from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = {"vsetv.com", "www.vsetv.com"}


async def render_schedule_html(url: str, *, timeout_ms: int = 12000) -> tuple[str | None, str | None]:
    """Return rendered HTML and final URL; never treat an empty page as success."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in ALLOWED_HOSTS:
        return None, None
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("playwright fallback requested but package is not installed")
        return None, None

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = await browser.new_page(locale="ru-RU", timezone_id="Europe/Moscow")
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    await page.locator("div.prname2").first.wait_for(timeout=3500)
                except Exception:
                    pass
                final_url = page.url
                if urlparse(final_url).hostname not in ALLOWED_HOSTS:
                    return None, None
                html = await page.content()
                return (html, final_url) if "prname2" in html else (None, None)
            finally:
                await browser.close()
    except (Exception, asyncio.CancelledError) as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        logger.warning("browser schedule fallback failed: %s", type(exc).__name__)
        return None, None


def browser_fallback_enabled() -> bool:
    return os.getenv("SLP_PLAYWRIGHT_FALLBACK", "false").lower() in {"1", "true", "yes"}
