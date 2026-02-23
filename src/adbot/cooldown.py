"""Cooldown and dedupe guard for adbot responses."""

from __future__ import annotations

import hashlib
import time


class CooldownGuard:
    def __init__(self, cooldown_sec: int):
        self.cooldown_sec = int(cooldown_sec or 0)
        self._last_intent_trigger: dict[str, float] = {}
        self._last_message_trigger: dict[str, float] = {}

    def _intent_key(self, chat_id: int, intent_code: str) -> str:
        return f"{chat_id}:{intent_code}"

    def _message_key(self, chat_id: int, intent_code: str, message: str) -> str:
        msg_hash = hashlib.sha1((message or "").strip().lower().encode("utf-8")).hexdigest()[:12]
        return f"{chat_id}:{intent_code}:{msg_hash}"

    def _dedupe_window_sec(self) -> int:
        # Hash-based dedupe should stay short; primary anti-spam gate is intent cooldown.
        if self.cooldown_sec <= 0:
            return 0
        return max(30, min(self.cooldown_sec, 300))

    def allow(self, chat_id: int, intent_code: str, message: str) -> bool:
        if self.cooldown_sec <= 0:
            return True

        now = time.time()
        intent_key = self._intent_key(chat_id, intent_code)
        last_intent = self._last_intent_trigger.get(intent_key, 0.0)
        if now - last_intent < self.cooldown_sec:
            return False

        # Extra replay guard for exactly the same phrase (short window).
        dedupe_window = self._dedupe_window_sec()
        if dedupe_window > 0:
            message_key = self._message_key(chat_id, intent_code, message)
            last_message = self._last_message_trigger.get(message_key, 0.0)
            if now - last_message < dedupe_window:
                return False
            self._last_message_trigger[message_key] = now

        self._last_intent_trigger[intent_key] = now
        return True
