"""Config for adbot runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass


def parse_chat_ids(raw: str) -> tuple[int, ...]:
    if not raw:
        return ()
    out: list[int] = []
    for part in raw.replace(",", " ").split():
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return tuple(dict.fromkeys(out))


def parse_bool(raw: str, default: bool = False) -> bool:
    value = str(raw or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(frozen=True)
class AdbotConfig:
    enabled: bool
    test_mode: bool
    api_id: int
    api_hash: str
    string_session: str
    target_powerbot_username: str
    source_chat_ids: tuple[int, ...]
    internal_chat_id: int | None
    reply_cooldown_sec: int
    min_message_len: int
    max_message_len: int
    min_confidence: int
    pipeline_timeout_ms: int
    allow_self_outgoing_e2e: bool
    self_outgoing_prefix: str


def build_config() -> AdbotConfig:
    enabled = parse_bool(os.getenv("ADBOT_ENABLED", "0"), default=False)
    test_mode = parse_bool(os.getenv("ADBOT_TEST_MODE", "0"), default=False)

    api_id_raw = os.getenv("TELETHON_API_ID")
    if not api_id_raw:
        raise ValueError("TELETHON_API_ID is required for adbot")
    if not os.getenv("TELETHON_API_HASH"):
        raise ValueError("TELETHON_API_HASH is required for adbot")
    if not os.getenv("ADBOT_STRING_SESSION"):
        raise ValueError("ADBOT_STRING_SESSION is required for adbot")
    target_powerbot_username = os.getenv("ADBOT_TARGET_POWERBOT_USERNAME", "").strip().lstrip("@")
    if not target_powerbot_username:
        raise ValueError("ADBOT_TARGET_POWERBOT_USERNAME is required for adbot")

    source_raw = os.getenv("ADBOT_SOURCE_CHAT_IDS", "")
    source_chat_ids = parse_chat_ids(source_raw)
    if not source_chat_ids and not test_mode:
        raise ValueError("ADBOT_SOURCE_CHAT_IDS is required (non-test mode)")

    internal_raw = os.getenv("ADBOT_INTERNAL_CHAT_ID", "").strip()
    internal_chat_id: int | None = None
    if internal_raw:
        try:
            internal_chat_id = int(internal_raw)
        except ValueError:
            internal_chat_id = None
    if internal_chat_id is None and not test_mode:
        raise ValueError("ADBOT_INTERNAL_CHAT_ID is required (non-test mode)")

    allow_self_outgoing_e2e = parse_bool(
        os.getenv("ADBOT_ALLOW_SELF_OUTGOING_E2E", "0"),
        default=False,
    )
    self_outgoing_prefix = os.getenv("ADBOT_SELF_OUTGOING_PREFIX", "[E2E]").strip() or "[E2E]"

    return AdbotConfig(
        enabled=enabled,
        test_mode=test_mode,
        api_id=int(api_id_raw),
        api_hash=os.getenv("TELETHON_API_HASH", "").strip(),
        string_session=os.getenv("ADBOT_STRING_SESSION", "").strip(),
        target_powerbot_username=target_powerbot_username,
        source_chat_ids=source_chat_ids,
        internal_chat_id=internal_chat_id,
        reply_cooldown_sec=int(os.getenv("ADBOT_REPLY_COOLDOWN_SEC", "10800")),
        min_message_len=int(os.getenv("ADBOT_MIN_MESSAGE_LEN", "12")),
        max_message_len=int(os.getenv("ADBOT_MAX_MESSAGE_LEN", "280")),
        min_confidence=int(os.getenv("ADBOT_MIN_CONFIDENCE", "120")),
        pipeline_timeout_ms=int(os.getenv("ADBOT_PIPELINE_TIMEOUT_MS", "5000")),
        allow_self_outgoing_e2e=allow_self_outgoing_e2e,
        self_outgoing_prefix=self_outgoing_prefix,
    )
