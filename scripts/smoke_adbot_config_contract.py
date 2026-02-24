#!/usr/bin/env python3
"""
Smoke for adbot config contract.

Checks:
- required Telethon/session vars are enforced;
- non-test mode requires source-chat allowlist;
- test mode allows empty source-chat allowlist;
- chat-id parsing deduplicates and ignores invalid tokens.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _bootstrap_imports() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))


@contextmanager
def _patched_env(**updates: str):
    old_values = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        yield
    finally:
        for key, old in old_values.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def _base_env() -> dict[str, str]:
    return {
        "ADBOT_ENABLED": "1",
        "ADBOT_TEST_MODE": "0",
        "TELETHON_API_ID": "33083266",
        "TELETHON_API_HASH": "87fe44a26a1a986054273f1d58ab7f3f",
        "ADBOT_STRING_SESSION": "valid-string-session-placeholder",
        "ADBOT_SOURCE_CHAT_IDS": "-100111,-100222",
        "ADBOT_INTERNAL_CHAT_ID": "-100333",
        "ADBOT_TARGET_POWERBOT_USERNAME": "TestNaButlerBot",
        "ADBOT_REPLY_COOLDOWN_SEC": "10800",
        "ADBOT_MIN_MESSAGE_LEN": "14",
        "ADBOT_MAX_MESSAGE_LEN": "280",
        "ADBOT_MIN_CONFIDENCE": "120",
        "ADBOT_PIPELINE_TIMEOUT_MS": "5000",
        "ADBOT_INTERNAL_REPLY_TIMEOUT_SEC": "8",
        "ADBOT_INTERNAL_MIN_NONEMPTY_LEN": "10",
        "ADBOT_INTERNAL_REQUIRE_REAL_BOT_REPLY": "1",
        "ADBOT_INTERNAL_ALLOWED_RESIDENT_BOT_IDS": "12345 67890",
    }


def main() -> None:
    _bootstrap_imports()
    from adbot_main_config import build_config, parse_chat_ids

    # parse_chat_ids contract
    parsed = parse_chat_ids("-1001, -1002 bad-token 1003 -1001")
    _assert(parsed == (-1001, -1002, 1003), f"unexpected parse_chat_ids result: {parsed}")

    # Valid non-test config
    with _patched_env(**_base_env()):
        cfg = build_config()
        _assert(cfg.enabled is True, "enabled should parse to True")
        _assert(cfg.test_mode is False, "test_mode should parse to False")
        _assert(cfg.source_chat_ids == (-100111, -100222), f"unexpected source ids: {cfg.source_chat_ids}")
        _assert(cfg.internal_chat_id == -100333, "internal chat id should parse")
        _assert(cfg.target_powerbot_username == "TestNaButlerBot", "unexpected target bot username")
        _assert(cfg.allow_self_outgoing_e2e is False, "self-outgoing e2e must be disabled by default")
        _assert(cfg.self_outgoing_prefix == "[E2E]", "unexpected default self-outgoing prefix")
        _assert(abs(cfg.self_outgoing_poll_sec - 1.5) < 1e-9, "unexpected default self-outgoing poll interval")
        _assert(cfg.internal_reply_timeout_sec == 8, "unexpected internal reply timeout")
        _assert(cfg.internal_min_nonempty_len == 10, "unexpected internal min nonempty len")
        _assert(cfg.internal_require_real_bot_reply is True, "internal real reply must default true")
        _assert(
            cfg.internal_allowed_resident_bot_ids == (12345, 67890),
            f"unexpected internal allowed bot ids: {cfg.internal_allowed_resident_bot_ids}",
        )

    # Username should be sanitized from leading @.
    with _patched_env(**{**_base_env(), "ADBOT_TARGET_POWERBOT_USERNAME": "@TestNaButlerBot"}):
        cfg = build_config()
        _assert(cfg.target_powerbot_username == "TestNaButlerBot", "target bot username should be normalized")

    # Non-test mode with empty allowlist must fail.
    with _patched_env(**{**_base_env(), "ADBOT_TEST_MODE": "0", "ADBOT_SOURCE_CHAT_IDS": ""}):
        try:
            build_config()
        except ValueError as exc:
            _assert("ADBOT_SOURCE_CHAT_IDS" in str(exc), f"unexpected error: {exc}")
        else:
            raise AssertionError("expected ValueError for non-test mode without source chat ids")

    # Non-test mode with missing internal audit chat must fail.
    with _patched_env(**{**_base_env(), "ADBOT_TEST_MODE": "0", "ADBOT_INTERNAL_CHAT_ID": ""}):
        try:
            build_config()
        except ValueError as exc:
            _assert("ADBOT_INTERNAL_CHAT_ID" in str(exc), f"unexpected error: {exc}")
        else:
            raise AssertionError("expected ValueError for non-test mode without internal chat id")

    # Test mode with empty allowlist is allowed.
    with _patched_env(
        **{
            **_base_env(),
            "ADBOT_TEST_MODE": "1",
            "ADBOT_SOURCE_CHAT_IDS": "",
            "ADBOT_INTERNAL_CHAT_ID": "",
        }
    ):
        cfg = build_config()
        _assert(cfg.test_mode is True, "test_mode should be True")
        _assert(cfg.source_chat_ids == (), f"source ids should be empty in test mode: {cfg.source_chat_ids}")
        _assert(cfg.internal_chat_id is None, "internal chat id may be empty in test mode")

    # Missing required vars must fail.
    for missing_key in (
        "TELETHON_API_ID",
        "TELETHON_API_HASH",
        "ADBOT_STRING_SESSION",
        "ADBOT_TARGET_POWERBOT_USERNAME",
    ):
        env = _base_env()
        env[missing_key] = None  # type: ignore[assignment]
        with _patched_env(**env):
            try:
                build_config()
            except ValueError:
                pass
            else:
                raise AssertionError(f"expected ValueError when missing {missing_key}")

    print("OK: adbot config contract smoke passed.")


if __name__ == "__main__":
    main()
