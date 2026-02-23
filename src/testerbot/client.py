"""Utility helpers for Testerbot Telegram userbot runtime."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


def _require_env(name: str) -> str:
    value = str(os.getenv(name, "")).strip()
    if not value:
        raise ValueError(f"Missing required env: {name}")
    return value


def parse_chat_ids(raw: str) -> tuple[int, ...]:
    """Parse comma/space-separated chat IDs (supports negative IDs)."""
    if not raw:
        return ()
    tokens = re.split(r"[,\s]+", raw.strip())
    ids: list[int] = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        try:
            ids.append(int(token))
        except ValueError:
            continue
    return tuple(dict.fromkeys(ids))


def parse_bool(raw: str, default: bool = False) -> bool:
    if raw is None:
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on", "y"}:
        return True
    if normalized in {"0", "false", "no", "off", "n"}:
        return False
    return default


def parse_csv_tokens(raw: str) -> tuple[str, ...]:
    if not raw:
        return ()
    tokens = re.split(r"[,\n]+", raw.strip())
    values: list[str] = []
    for token in tokens:
        value = token.strip()
        if not value:
            continue
        values.append(value)
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True)
class TesterbotTargets:
    powerbot: str
    adminbot: str
    businessbot: str

    @classmethod
    def from_env(cls) -> "TesterbotTargets":
        return cls(
            powerbot=_require_env("TESTERBOT_TARGET_POWERBOT_USERNAME").lstrip("@").strip(),
            adminbot=_require_env("TESTERBOT_TARGET_ADMINBOT_USERNAME").lstrip("@").strip(),
            businessbot=_require_env("TESTERBOT_TARGET_BUSINESSBOT_USERNAME").lstrip("@").strip(),
        )


@dataclass(frozen=True)
class TesterbotConfig:
    api_id: int
    api_hash: str
    string_session: str
    targets: TesterbotTargets
    allowed_chat_ids: tuple[int, ...]
    timeout_sec: int
    report_path: str
    building_label: str
    section_label: str
    db_path: str
    idempotence_tables: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "TesterbotConfig":
        return cls(
            api_id=int(_require_env("TELETHON_API_ID")),
            api_hash=_require_env("TELETHON_API_HASH"),
            string_session=_require_env("TESTERBOT_STRING_SESSION"),
            targets=TesterbotTargets.from_env(),
            allowed_chat_ids=parse_chat_ids(os.getenv("TESTERBOT_ALLOWED_CHAT_IDS", "")),
            timeout_sec=int(os.getenv("TESTERBOT_TIMEOUT_SEC", "25")),
            report_path=os.getenv("TESTERBOT_REPORT_PATH", "/data/logs/testerbot_results.json").strip(),
            building_label=os.getenv("TESTERBOT_BUILDING_LABEL", "Ньюкасл (24-в)").strip(),
            section_label=os.getenv("TESTERBOT_SECTION_LABEL", "2 секція").strip(),
            db_path=(
                os.getenv("TESTERBOT_DB_PATH", "").strip()
                or os.getenv("DB_PATH", "").strip()
                or "/data/state.db"
            ),
            idempotence_tables=parse_csv_tokens(
                os.getenv(
                    "TESTERBOT_IDEMPOTENCE_TABLES",
                    "business_owners,business_subscriptions,business_subscription_periods,"
                    "business_payment_events,business_claim_tokens,place_reports,admin_jobs",
                )
            ),
        )


def ensure_enabled() -> bool:
    return parse_bool(os.getenv("TESTERBOT_ENABLED", "0"), default=False)


def build_telethon_client(cfg: TesterbotConfig):
    """Build and return a Telethon client instance (if dependency is installed)."""
    try:
        from telethon import TelegramClient  # type: ignore
        from telethon.sessions import StringSession  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Telethon is required for testerbot. Add requirements-dev.txt dependencies or image runtime."
        ) from exc
    return TelegramClient(StringSession(cfg.string_session), cfg.api_id, cfg.api_hash)
