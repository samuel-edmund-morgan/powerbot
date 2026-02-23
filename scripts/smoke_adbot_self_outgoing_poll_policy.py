#!/usr/bin/env python3
"""
Static policy smoke for adbot self-outgoing poll fallback.

Guards that same-session E2E support remains wired:
- fallback poll loop function exists;
- poll loop task is created under allow_self_outgoing_e2e gate;
- poll interval comes from config/self-outgoing env contract.
"""

from __future__ import annotations

from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    main_src = (repo_root / "src" / "adbot_main.py").read_text(encoding="utf-8")
    cfg_src = (repo_root / "src" / "adbot_main_config.py").read_text(encoding="utf-8")

    _assert(
        "async def _run_self_outgoing_poll_loop(" in main_src,
        "missing _run_self_outgoing_poll_loop function",
    )
    _assert(
        "adbot-self-outgoing-poll" in main_src,
        "missing named poll task for self-outgoing fallback",
    )
    _assert(
        "config.allow_self_outgoing_e2e and source_chat_ids and self_user_id" in main_src,
        "missing poll task gate (allow_self_outgoing_e2e + source chats + self user id)",
    )
    _assert(
        "poll_sec=config.self_outgoing_poll_sec" in main_src,
        "missing poll interval wiring from config",
    )
    _assert(
        "if is_outgoing and sender_id != self_user_id:" in main_src,
        "missing outgoing sender guard for poll fallback",
    )
    _assert(
        "if not text.startswith(prefix):" in main_src,
        "missing prefix guard in poll fallback",
    )
    _assert(
        "ADBOT_SELF_OUTGOING_POLL_SEC" in cfg_src,
        "missing ADBOT_SELF_OUTGOING_POLL_SEC config key",
    )

    print("OK: adbot self-outgoing poll policy smoke passed.")


if __name__ == "__main__":
    main()
