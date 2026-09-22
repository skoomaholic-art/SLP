from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

import aiohttp


DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "~openai/gpt-latest"


class OpenRouterError(RuntimeError):
    """Raised when OpenRouter is unavailable or returns an invalid response."""


def _extract_text(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise OpenRouterError("OpenRouter response does not contain assistant content") from error

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if parts:
            return "\n".join(parts)

    raise OpenRouterError("OpenRouter returned unsupported assistant content")


@dataclass(slots=True)
class OpenRouterClient:
    api_key: str
    model: str = DEFAULT_OPENROUTER_MODEL
    base_url: str = DEFAULT_OPENROUTER_BASE_URL
    site_url: str = ""
    app_title: str = "SLP"
    timeout_seconds: int = 60

    @classmethod
    def from_env(cls) -> "OpenRouterClient":
        return cls(
            api_key=str(os.getenv("OPENROUTER_API_KEY", "")).strip(),
            model=str(os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)).strip()
            or DEFAULT_OPENROUTER_MODEL,
            base_url=str(
                os.getenv("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL)
            ).strip().rstrip("/")
            or DEFAULT_OPENROUTER_BASE_URL,
            site_url=str(os.getenv("OPENROUTER_SITE_URL", "")).strip(),
            app_title=str(os.getenv("OPENROUTER_APP_TITLE", "SLP")).strip() or "SLP",
        )

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise OpenRouterError(
                "OPENROUTER_API_KEY не настроен. Добавьте его в секреты окружения."
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.app_title:
            headers["X-OpenRouter-Title"] = self.app_title
        return headers

    async def chat(
        self,
        messages: Iterable[dict[str, Any]],
        *,
        model: str | None = None,
        **parameters: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": list(messages),
            **parameters,
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        endpoint = f"{self.base_url}/chat/completions"

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                endpoint,
                headers=self._headers(),
                json=payload,
            ) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    message = (
                        data.get("error", {}).get("message")
                        if isinstance(data, dict)
                        and isinstance(data.get("error"), dict)
                        else None
                    )
                    raise OpenRouterError(
                        message or f"OpenRouter API returned HTTP {response.status}"
                    )

        if not isinstance(data, dict):
            raise OpenRouterError("OpenRouter returned a non-object response")
        return data

    async def ask(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        **parameters: Any,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return _extract_text(
            await self.chat(messages, model=model, **parameters)
        )
