"""adbot response pipeline."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class InternalReplyResult:
    text: str | None
    reason: str | None
    internal_message_id: int | None
    via_bot_id: int | None


class InternalReplyPipeline:
    """Get resident-bot response via internal chat inline-insert flow."""

    def __init__(
        self,
        *,
        tg_client,
        target_powerbot_username: str,
        timeout_sec: int = 8,
        min_nonempty_len: int = 10,
        require_real: bool = True,
        allowed_resident_bot_ids: tuple[int, ...] = (),
    ):
        self._client = tg_client
        self._target_powerbot_username = str(target_powerbot_username or "").strip().lstrip("@")
        self._timeout_sec = max(int(timeout_sec or 1), 1)
        self._min_nonempty_len = max(int(min_nonempty_len or 1), 1)
        self._require_real = bool(require_real)
        self._allowed_resident_bot_ids = tuple(int(v) for v in (allowed_resident_bot_ids or ()) if int(v) > 0)

    @staticmethod
    def _message_text(message_obj: object | None) -> str:
        if message_obj is None:
            return ""
        return str(
            getattr(message_obj, "raw_text", "")
            or getattr(message_obj, "text", "")
            or getattr(message_obj, "message", "")
            or ""
        ).strip()

    async def _click_inline_to_internal(
        self,
        *,
        query: str,
        internal_chat_id: int,
        reply_to_message_id: int | None,
    ) -> object | None:
        if not query:
            return None
        results = await self._client.inline_query(self._target_powerbot_username, query)
        if not results or not getattr(results, "results", None):
            return None

        first = results.results[0]
        async def _click(use_reply_to: bool) -> object | None:
            try:
                if use_reply_to and reply_to_message_id:
                    return await first.click(entity=internal_chat_id, reply_to=reply_to_message_id)
                return await first.click(entity=internal_chat_id)
            except TypeError:
                # Some Telethon versions don't accept named args in click(...).
                if use_reply_to and reply_to_message_id:
                    return await first.click(internal_chat_id, reply_to_message_id)
                return await first.click(internal_chat_id)

        if reply_to_message_id:
            try:
                return await _click(True)
            except Exception:
                # Fallback: some chats/contexts can reject reply_to linkage.
                # Retry plain inline insertion to keep E2E flow deterministic.
                logger.warning(
                    "internal inline click with reply_to failed; retrying without reply_to (chat_id=%s reply_to=%s)",
                    internal_chat_id,
                    reply_to_message_id,
                    exc_info=True,
                )
                return await _click(False)

        return await _click(False)

    async def get_via_internal(
        self,
        *,
        query: str,
        fallback: str,
        internal_chat_id: int,
        reply_to_message_id: int | None = None,
    ) -> InternalReplyResult:
        if not internal_chat_id:
            return InternalReplyResult(
                text=(fallback if not self._require_real else None),
                reason="internal_timeout",
                internal_message_id=None,
                via_bot_id=None,
            )

        try:
            inserted = await asyncio.wait_for(
                self._click_inline_to_internal(
                    query=query,
                    internal_chat_id=int(internal_chat_id),
                    reply_to_message_id=reply_to_message_id,
                ),
                timeout=self._timeout_sec,
            )
        except asyncio.TimeoutError:
            return InternalReplyResult(
                text=(fallback if not self._require_real else None),
                reason="internal_timeout",
                internal_message_id=None,
                via_bot_id=None,
            )
        except Exception:
            return InternalReplyResult(
                text=(fallback if not self._require_real else None),
                reason="copied_reply_failed",
                internal_message_id=None,
                via_bot_id=None,
            )

        if inserted is None:
            return InternalReplyResult(
                text=(fallback if not self._require_real else None),
                reason="inline_empty",
                internal_message_id=None,
                via_bot_id=None,
            )

        text = self._message_text(inserted)
        internal_message_id = int(getattr(inserted, "id", 0) or 0) or None
        via_bot_id = int(getattr(inserted, "via_bot_id", 0) or 0) or None
        if self._allowed_resident_bot_ids and via_bot_id not in set(self._allowed_resident_bot_ids):
            return InternalReplyResult(
                text=(fallback if not self._require_real else None),
                reason="resident_no_reply",
                internal_message_id=internal_message_id,
                via_bot_id=via_bot_id,
            )

        if len(text) < self._min_nonempty_len:
            return InternalReplyResult(
                text=(fallback if not self._require_real else None),
                reason="resident_no_reply",
                internal_message_id=internal_message_id,
                via_bot_id=via_bot_id,
            )

        return InternalReplyResult(
            text=text,
            reason=None,
            internal_message_id=internal_message_id,
            via_bot_id=via_bot_id,
        )
