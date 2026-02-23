#!/usr/bin/env python3
"""
Dynamic smoke for adbot cooldown/dedupe contract.

Contract:
- primary cooldown is per (chat_id, intent);
- different phrasing of same intent in same chat is still suppressed during cooldown;
- same intent in another chat is allowed;
- cooldown=0 disables guard (test-mode friendliness).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _bootstrap_imports() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))


def main() -> None:
    _bootstrap_imports()
    from adbot.cooldown import CooldownGuard

    guard = CooldownGuard(2)

    chat_id = -1001
    intent = "electrician"
    msg_a = "Дайте номер електрика"
    msg_b = "Хто має телефон електрика?"

    # First hit allowed.
    _assert(guard.allow(chat_id, intent, msg_a) is True, "first trigger must pass")
    # Same intent + different text in same chat must be blocked by intent-level cooldown.
    _assert(
        guard.allow(chat_id, intent, msg_b) is False,
        "same intent in same chat must be blocked despite different wording",
    )
    # Same intent in different chat is independent and should pass.
    _assert(
        guard.allow(chat_id - 1, intent, msg_b) is True,
        "different chat should have independent cooldown bucket",
    )
    # Different intent in same chat is independent and should pass.
    _assert(
        guard.allow(chat_id, "light_status", "Чи є світло в Ньюкасл?") is True,
        "different intent in same chat should not be blocked",
    )

    # After cooldown expires, same intent in same chat should pass again.
    time.sleep(2.05)
    _assert(
        guard.allow(chat_id, intent, msg_b) is True,
        "intent cooldown should expire and allow new trigger",
    )

    # Cooldown disabled -> always allowed.
    disabled = CooldownGuard(0)
    _assert(disabled.allow(chat_id, intent, msg_a) is True, "disabled guard first allow")
    _assert(disabled.allow(chat_id, intent, msg_a) is True, "disabled guard repeated allow")

    print("OK: adbot cooldown contract smoke passed.")


if __name__ == "__main__":
    main()
