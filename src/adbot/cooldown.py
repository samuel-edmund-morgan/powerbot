"""Cooldown and dedupe guard for adbot responses."""

from __future__ import annotations

import hashlib
import time


class CooldownGuard:
    def __init__(self, cooldown_sec: int):
        self.cooldown_sec = int(cooldown_sec or 0)
        self._last_trigger: dict[str, float] = {}

    def _key(self, chat_id: int, intent_code: str, message: str) -> str:
        msg_hash = hashlib.sha1((message or "").strip().lower().encode("utf-8")).hexdigest()[:12]
        return f"{chat_id}:{intent_code}:{msg_hash}"

    def allow(self, chat_id: int, intent_code: str, message: str) -> bool:
        if self.cooldown_sec <= 0:
            return True

        key = self._key(chat_id, intent_code, message)
        now = time.time()
        last = self._last_trigger.get(key, 0.0)
        if now - last < self.cooldown_sec:
            return False
        self._last_trigger[key] = now
        return True
