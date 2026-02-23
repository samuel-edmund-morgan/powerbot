"""adbot response pipeline."""

from __future__ import annotations

import asyncio
from typing import Optional


class PowerbotInlineClient:
    """Wrapper over Telethon client to get content via inline query."""

    def __init__(self, tg_client, bot_username: str):
        self._client = tg_client
        self._bot_username = bot_username.lstrip("@")

    @staticmethod
    def _is_fallback_article(article: object) -> bool:
        text = str(getattr(article, "title", "") or "").strip().lower()
        desc = str(getattr(article, "description", "") or "").strip().lower()
        return (
            text.startswith("нічого не знайдено")
            or "по запиту" in desc
            or "нікого" in text
            or "нічого" == text
        )

    async def fetch_block(self, query: str) -> Optional[str]:
        try:
            results = await self._client.inline_query(self._bot_username, query)
            if not results or not getattr(results, "results", None):
                return None
            first = results.results[0]
            if self._is_fallback_article(first):
                return None
            title = str(getattr(first, "title", "")).strip()
            desc = str(getattr(first, "description", "")).strip()
            if title and desc:
                return f"{title}\n{desc}"
            if title:
                return title
            if desc:
                return desc
            return None
        except Exception:
            return None


class ResponsePipeline:
    def __init__(self, answer_provider: PowerbotInlineClient, fallback_ms: int = 5_000):
        self._answer_provider = answer_provider
        self._fallback_ms = fallback_ms

    async def answer(self, query: str, fallback: str) -> str:
        try:
            inline_text = await asyncio.wait_for(
                self._answer_provider.fetch_block(query),
                timeout=max(self._fallback_ms, 500) / 1000,
            )
            if inline_text:
                return inline_text
            return fallback
        except asyncio.TimeoutError:
            return fallback
        except Exception:
            return fallback
