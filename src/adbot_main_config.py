"""Config for adbot runtime."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


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


def chat_id_variants(chat_id: int) -> tuple[int, ...]:
    value = int(chat_id or 0)
    if value == 0:
        return (0,)

    variants: list[int] = [value]
    raw = str(abs(value))

    # Bot API supergroup/channel ID -> Telethon short ID
    if value < 0 and raw.startswith("100") and len(raw) > 3:
        try:
            variants.append(-int(raw[3:]))
        except Exception:
            pass

    # Telethon short ID -> Bot API supergroup/channel ID
    if value < 0 and not raw.startswith("100"):
        try:
            variants.append(-int(f"100{raw}"))
        except Exception:
            pass

    return tuple(dict.fromkeys(variants))


def parse_light_chat_bindings(raw: str) -> dict[int, tuple[int, int]]:
    """Parse chat->(building,section) bindings from env.

    Format examples:
    - "-100111:1:2,-100222:13:1"
    - "-100111:1:2 -100222:13:1"
    """
    out: dict[int, tuple[int, int]] = {}
    if not raw:
        return out
    normalized = str(raw).replace(",", " ").replace(";", " ")
    for part in normalized.split():
        token = part.strip()
        if not token:
            continue
        bits = token.split(":")
        if len(bits) != 3:
            continue
        try:
            chat_id = int(bits[0].strip())
            building_id = int(bits[1].strip())
            section_id = int(bits[2].strip())
        except ValueError:
            continue
        if chat_id == 0 or building_id <= 0 or section_id <= 0:
            continue
        out[int(chat_id)] = (int(building_id), int(section_id))
    return out


def parse_bool(raw: str, default: bool = False) -> bool:
    value = str(raw or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_int(raw: str) -> int | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _parse_pair_count(raw: str) -> int:
    parsed = _parse_int(raw)
    if parsed is None:
        return 0
    return max(int(parsed), 0)


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
    pair_mode: bool
    chat_pairs: tuple["AdbotChatPair", ...]
    light_chat_bindings: dict[int, tuple[int, int]]
    reply_cooldown_sec: int
    min_message_len: int
    max_message_len: int
    min_confidence: int
    pipeline_timeout_ms: int
    internal_reply_timeout_sec: int
    internal_min_nonempty_len: int
    internal_require_real_bot_reply: bool
    source_require_forwarded: bool
    source_allow_text_fallback_on_forward_failure: bool
    internal_allowed_resident_bot_ids: tuple[int, ...]
    allow_self_outgoing_e2e: bool
    self_outgoing_prefix: str
    self_outgoing_poll_sec: float


@dataclass(frozen=True)
class AdbotChatPair:
    idx: int
    source_chat_id: int
    internal_chat_id: int
    sensor_uuid: str
    fallback_building_id: int
    fallback_section_id: int
    reply_cooldown_sec: int
    label: str


def _pair_parse_error(*, idx: int, errors: list[str], test_mode: bool) -> None:
    message = f"ADBOT_PAIR_{idx} invalid: " + "; ".join(errors)
    if test_mode:
        logger.warning("%s (skipped in test mode)", message)
        return
    raise ValueError(message)


def _parse_one_pair(
    *,
    idx: int,
    test_mode: bool,
    default_cooldown_sec: int,
) -> AdbotChatPair | None:
    source_chat_id = _parse_int(os.getenv(f"ADBOT_PAIR_{idx}_SOURCE_CHAT_ID", ""))
    internal_chat_id = _parse_int(os.getenv(f"ADBOT_PAIR_{idx}_INTERNAL_CHAT_ID", ""))
    sensor_uuid = str(os.getenv(f"ADBOT_PAIR_{idx}_SENSOR_UUID", "")).strip()
    fallback_building_id = _parse_int(os.getenv(f"ADBOT_PAIR_{idx}_FALLBACK_BUILDING_ID", ""))
    fallback_section_id = _parse_int(os.getenv(f"ADBOT_PAIR_{idx}_FALLBACK_SECTION_ID", ""))
    label = str(os.getenv(f"ADBOT_PAIR_{idx}_LABEL", "")).strip() or f"pair_{idx}"
    pair_cooldown_raw = str(os.getenv(f"ADBOT_PAIR_{idx}_REPLY_COOLDOWN_SEC", "")).strip()
    pair_cooldown = _parse_int(pair_cooldown_raw)

    errors: list[str] = []
    if source_chat_id is None:
        errors.append("SOURCE_CHAT_ID must be numeric")
    if internal_chat_id is None:
        errors.append("INTERNAL_CHAT_ID must be numeric")
    if not sensor_uuid:
        errors.append("SENSOR_UUID is required")
    if fallback_building_id is None or fallback_building_id <= 0:
        errors.append("FALLBACK_BUILDING_ID must be > 0")
    if fallback_section_id is None or fallback_section_id <= 0:
        errors.append("FALLBACK_SECTION_ID must be > 0")
    if pair_cooldown_raw and pair_cooldown is None:
        errors.append("REPLY_COOLDOWN_SEC must be numeric")
    if pair_cooldown is not None and pair_cooldown < 0:
        errors.append("REPLY_COOLDOWN_SEC must be >= 0")

    if errors:
        _pair_parse_error(idx=idx, errors=errors, test_mode=test_mode)
        return None

    return AdbotChatPair(
        idx=int(idx),
        source_chat_id=int(source_chat_id),
        internal_chat_id=int(internal_chat_id),
        sensor_uuid=sensor_uuid,
        fallback_building_id=int(fallback_building_id),
        fallback_section_id=int(fallback_section_id),
        reply_cooldown_sec=int(pair_cooldown if pair_cooldown is not None else default_cooldown_sec),
        label=label,
    )


def parse_chat_pairs_from_env(
    *,
    test_mode: bool,
    default_cooldown_sec: int,
) -> tuple[AdbotChatPair, ...]:
    pair_count_raw = str(os.getenv("ADBOT_PAIR_COUNT", "0")).strip()
    parsed_pair_count = _parse_int(pair_count_raw)
    if pair_count_raw and parsed_pair_count is None:
        message = "ADBOT_PAIR_COUNT must be numeric"
        if test_mode:
            logger.warning("%s (pair-mode disabled in test mode)", message)
            return ()
        raise ValueError(message)
    pair_count = _parse_pair_count(pair_count_raw)
    if pair_count <= 0:
        return ()

    parsed: list[AdbotChatPair] = []
    for idx in range(1, pair_count + 1):
        pair = _parse_one_pair(
            idx=idx,
            test_mode=test_mode,
            default_cooldown_sec=default_cooldown_sec,
        )
        if pair is not None:
            parsed.append(pair)

    accepted: list[AdbotChatPair] = []
    seen_source_variant_to_idx: dict[int, int] = {}
    seen_internal_variant_to_idx: dict[int, int] = {}
    for pair in parsed:
        src_conflict: int | None = None
        for variant in chat_id_variants(pair.source_chat_id):
            if variant in seen_source_variant_to_idx:
                src_conflict = seen_source_variant_to_idx[variant]
                break

        internal_conflict: int | None = None
        for variant in chat_id_variants(pair.internal_chat_id):
            if variant in seen_internal_variant_to_idx:
                internal_conflict = seen_internal_variant_to_idx[variant]
                break

        if src_conflict is not None or internal_conflict is not None:
            errors: list[str] = []
            if src_conflict is not None:
                errors.append(f"SOURCE_CHAT_ID duplicates pair #{src_conflict}")
            if internal_conflict is not None:
                errors.append(f"INTERNAL_CHAT_ID duplicates pair #{internal_conflict}")
            _pair_parse_error(idx=pair.idx, errors=errors, test_mode=test_mode)
            continue

        accepted.append(pair)
        for variant in chat_id_variants(pair.source_chat_id):
            seen_source_variant_to_idx[int(variant)] = int(pair.idx)
        for variant in chat_id_variants(pair.internal_chat_id):
            seen_internal_variant_to_idx[int(variant)] = int(pair.idx)

    if pair_count > 0 and not accepted and not test_mode:
        raise ValueError("ADBOT_PAIR_COUNT>0 but no valid chat pairs were parsed")

    return tuple(accepted)


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

    global_reply_cooldown_sec = int(os.getenv("ADBOT_REPLY_COOLDOWN_SEC", "10800"))
    chat_pairs = parse_chat_pairs_from_env(
        test_mode=test_mode,
        default_cooldown_sec=global_reply_cooldown_sec,
    )
    pair_mode = len(chat_pairs) > 0

    source_raw = os.getenv("ADBOT_SOURCE_CHAT_IDS", "")
    source_chat_ids = parse_chat_ids(source_raw)
    internal_raw = os.getenv("ADBOT_INTERNAL_CHAT_ID", "").strip()
    internal_chat_id: int | None = _parse_int(internal_raw)

    if pair_mode:
        source_chat_ids = tuple(dict.fromkeys(int(pair.source_chat_id) for pair in chat_pairs))
    else:
        if not source_chat_ids and not test_mode:
            raise ValueError("ADBOT_SOURCE_CHAT_IDS is required (non-test mode)")
        if internal_chat_id is None and not test_mode:
            raise ValueError("ADBOT_INTERNAL_CHAT_ID is required (non-test mode)")

    light_chat_bindings = parse_light_chat_bindings(os.getenv("ADBOT_LIGHT_CHAT_BINDINGS", ""))
    if pair_mode and light_chat_bindings:
        logger.warning(
            "ADBOT_LIGHT_CHAT_BINDINGS is ignored because pair-mode is active (ADBOT_PAIR_COUNT>0)."
        )
        light_chat_bindings = {}

    allowed_resident_bot_ids = parse_chat_ids(os.getenv("ADBOT_INTERNAL_ALLOWED_RESIDENT_BOT_IDS", ""))

    allow_self_outgoing_e2e = parse_bool(
        os.getenv("ADBOT_ALLOW_SELF_OUTGOING_E2E", "0"),
        default=False,
    )
    self_outgoing_prefix = os.getenv("ADBOT_SELF_OUTGOING_PREFIX", "[E2E]").strip() or "[E2E]"
    poll_raw = str(os.getenv("ADBOT_SELF_OUTGOING_POLL_SEC", "1.5")).strip()
    try:
        self_outgoing_poll_sec = float(poll_raw)
    except ValueError:
        self_outgoing_poll_sec = 1.5

    return AdbotConfig(
        enabled=enabled,
        test_mode=test_mode,
        api_id=int(api_id_raw),
        api_hash=os.getenv("TELETHON_API_HASH", "").strip(),
        string_session=os.getenv("ADBOT_STRING_SESSION", "").strip(),
        target_powerbot_username=target_powerbot_username,
        source_chat_ids=source_chat_ids,
        internal_chat_id=internal_chat_id,
        pair_mode=pair_mode,
        chat_pairs=chat_pairs,
        light_chat_bindings=light_chat_bindings,
        reply_cooldown_sec=global_reply_cooldown_sec,
        min_message_len=int(os.getenv("ADBOT_MIN_MESSAGE_LEN", "12")),
        max_message_len=int(os.getenv("ADBOT_MAX_MESSAGE_LEN", "280")),
        min_confidence=int(os.getenv("ADBOT_MIN_CONFIDENCE", "120")),
        pipeline_timeout_ms=int(os.getenv("ADBOT_PIPELINE_TIMEOUT_MS", "5000")),
        internal_reply_timeout_sec=int(os.getenv("ADBOT_INTERNAL_REPLY_TIMEOUT_SEC", "8")),
        internal_min_nonempty_len=int(os.getenv("ADBOT_INTERNAL_MIN_NONEMPTY_LEN", "10")),
        internal_require_real_bot_reply=parse_bool(
            os.getenv("ADBOT_INTERNAL_REQUIRE_REAL_BOT_REPLY", "1"),
            default=True,
        ),
        source_require_forwarded=parse_bool(
            os.getenv("ADBOT_SOURCE_REQUIRE_FORWARDED", "0"),
            default=False,
        ),
        source_allow_text_fallback_on_forward_failure=parse_bool(
            os.getenv("ADBOT_SOURCE_ALLOW_TEXT_FALLBACK", "0"),
            default=False,
        ),
        internal_allowed_resident_bot_ids=allowed_resident_bot_ids,
        allow_self_outgoing_e2e=allow_self_outgoing_e2e,
        self_outgoing_prefix=self_outgoing_prefix,
        self_outgoing_poll_sec=max(self_outgoing_poll_sec, 0.5),
    )
