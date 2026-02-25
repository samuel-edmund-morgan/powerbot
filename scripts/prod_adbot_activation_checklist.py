#!/usr/bin/env python3
"""
Prod adbot activation checklist.

Purpose:
- run a deterministic preflight before enabling adbot in prod;
- fail fast when required env values are missing/placeholder/invalid.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def _load_env(env_file: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        data[key] = _strip_quotes(value)
    return data


def _flag_true(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_placeholder(value: str) -> bool:
    lower = value.strip().lower()
    if not lower:
        return True
    return any(
        marker in lower
        for marker in (
            "your",
            "placeholder",
            "example",
            "changeme",
            "replace",
        )
    )


def _parse_chat_ids(raw: str) -> tuple[int, ...]:
    values: list[int] = []
    seen: set[int] = set()
    for token in re.split(r"[\s,]+", raw.strip()):
        if not token:
            continue
        if not re.fullmatch(r"-?[0-9]+", token):
            continue
        parsed = int(token)
        if parsed in seen:
            continue
        values.append(parsed)
        seen.add(parsed)
    return tuple(values)


def _parse_int(raw: str) -> int | None:
    token = str(raw or "").strip()
    if not token or not re.fullmatch(r"-?[0-9]+", token):
        return None
    try:
        return int(token)
    except Exception:
        return None


def _chat_id_variants(chat_id: int) -> tuple[int, ...]:
    value = int(chat_id or 0)
    if value == 0:
        return (0,)
    out: list[int] = [value]
    raw = str(abs(value))
    if value < 0 and raw.startswith("100") and len(raw) > 3:
        try:
            out.append(-int(raw[3:]))
        except Exception:
            pass
    if value < 0 and not raw.startswith("100"):
        try:
            out.append(-int(f"100{raw}"))
        except Exception:
            pass
    return tuple(dict.fromkeys(out))


def _print_result(ok: bool, name: str, details: str) -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}: {details}")


def _require(env: dict[str, str], key: str) -> str:
    return str(env.get(key, "")).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Prod adbot activation checklist")
    parser.add_argument(
        "--env-file",
        default="/opt/powerbot/.env",
        help="Path to prod .env file",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any check fails (default behavior).",
    )
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="Always return zero exit code (report-only mode).",
    )
    args = parser.parse_args()

    strict = True
    if args.no_strict:
        strict = False
    elif args.strict:
        strict = True

    env_file = Path(args.env_file)
    if not env_file.exists():
        raise SystemExit(f"ERROR: env file not found: {env_file}")

    env = _load_env(env_file)
    failures: list[str] = []

    print("Adbot prod activation checklist")
    print(f"- env_file: {env_file}")

    adbot_enabled = _flag_true(_require(env, "ADBOT_ENABLED"))
    _print_result(adbot_enabled, "ADBOT_ENABLED", "must be 1 for prod activation")
    if not adbot_enabled:
        failures.append("ADBOT_ENABLED")

    adbot_test_mode = _flag_true(_require(env, "ADBOT_TEST_MODE"))
    _print_result(not adbot_test_mode, "ADBOT_TEST_MODE", "must be 0 in prod")
    if adbot_test_mode:
        failures.append("ADBOT_TEST_MODE")

    api_id = _require(env, "TELETHON_API_ID")
    api_id_ok = bool(re.fullmatch(r"[0-9]+", api_id))
    _print_result(api_id_ok, "TELETHON_API_ID", "must be numeric")
    if not api_id_ok:
        failures.append("TELETHON_API_ID")

    api_hash = _require(env, "TELETHON_API_HASH")
    api_hash_ok = not _is_placeholder(api_hash)
    _print_result(api_hash_ok, "TELETHON_API_HASH", "must be non-empty/non-placeholder")
    if not api_hash_ok:
        failures.append("TELETHON_API_HASH")

    session = _require(env, "ADBOT_STRING_SESSION")
    session_ok = (not _is_placeholder(session)) and len(session) >= 16
    _print_result(session_ok, "ADBOT_STRING_SESSION", "must be valid non-placeholder string session")
    if not session_ok:
        failures.append("ADBOT_STRING_SESSION")

    target_username = _require(env, "ADBOT_TARGET_POWERBOT_USERNAME").lstrip("@")
    username_ok = (not _is_placeholder(target_username)) and bool(re.fullmatch(r"[A-Za-z0-9_]{5,}", target_username))
    _print_result(username_ok, "ADBOT_TARGET_POWERBOT_USERNAME", "must be valid bot username")
    if not username_ok:
        failures.append("ADBOT_TARGET_POWERBOT_USERNAME")

    pair_count = _parse_int(_require(env, "ADBOT_PAIR_COUNT"))
    pair_count = max(int(pair_count or 0), 0)
    pair_mode = pair_count > 0
    _print_result(True, "ADBOT_ROUTING_MODE", f"{'pair' if pair_mode else 'legacy'}")

    if pair_mode:
        seen_source_variants: dict[int, int] = {}
        seen_internal_variants: dict[int, int] = {}
        for idx in range(1, pair_count + 1):
            source_key = f"ADBOT_PAIR_{idx}_SOURCE_CHAT_ID"
            internal_key = f"ADBOT_PAIR_{idx}_INTERNAL_CHAT_ID"
            sensor_key = f"ADBOT_PAIR_{idx}_SENSOR_UUID"
            fallback_building_key = f"ADBOT_PAIR_{idx}_FALLBACK_BUILDING_ID"
            fallback_section_key = f"ADBOT_PAIR_{idx}_FALLBACK_SECTION_ID"
            cooldown_key = f"ADBOT_PAIR_{idx}_REPLY_COOLDOWN_SEC"

            source_chat_id = _parse_int(_require(env, source_key))
            source_ok = source_chat_id is not None
            _print_result(source_ok, source_key, "must be a numeric chat_id")
            if not source_ok:
                failures.append(source_key)

            internal_chat_id = _parse_int(_require(env, internal_key))
            internal_ok = internal_chat_id is not None
            _print_result(internal_ok, internal_key, "must be a numeric chat_id")
            if not internal_ok:
                failures.append(internal_key)

            sensor_uuid = _require(env, sensor_key)
            sensor_ok = not _is_placeholder(sensor_uuid)
            _print_result(sensor_ok, sensor_key, "must be non-empty/non-placeholder")
            if not sensor_ok:
                failures.append(sensor_key)

            fallback_building = _parse_int(_require(env, fallback_building_key))
            fallback_building_ok = fallback_building is not None and int(fallback_building) > 0
            _print_result(fallback_building_ok, fallback_building_key, "must be integer > 0")
            if not fallback_building_ok:
                failures.append(fallback_building_key)

            fallback_section = _parse_int(_require(env, fallback_section_key))
            fallback_section_ok = fallback_section is not None and int(fallback_section) > 0
            _print_result(fallback_section_ok, fallback_section_key, "must be integer > 0")
            if not fallback_section_ok:
                failures.append(fallback_section_key)

            cooldown_raw = _require(env, cooldown_key)
            cooldown_value = _parse_int(cooldown_raw) if cooldown_raw else 0
            cooldown_ok = (not cooldown_raw) or (cooldown_value is not None and int(cooldown_value) >= 0)
            _print_result(cooldown_ok, cooldown_key, "optional; if set must be integer >= 0")
            if not cooldown_ok:
                failures.append(cooldown_key)

            if source_chat_id is not None:
                for variant in _chat_id_variants(int(source_chat_id)):
                    if variant in seen_source_variants:
                        _print_result(
                            False,
                            source_key,
                            f"duplicates pair #{seen_source_variants[variant]} (including chat-id variants)",
                        )
                        failures.append(source_key)
                        break
                for variant in _chat_id_variants(int(source_chat_id)):
                    seen_source_variants[int(variant)] = int(idx)

            if internal_chat_id is not None:
                for variant in _chat_id_variants(int(internal_chat_id)):
                    if variant in seen_internal_variants:
                        _print_result(
                            False,
                            internal_key,
                            f"duplicates pair #{seen_internal_variants[variant]} (including chat-id variants)",
                        )
                        failures.append(internal_key)
                        break
                for variant in _chat_id_variants(int(internal_chat_id)):
                    seen_internal_variants[int(variant)] = int(idx)
    else:
        source_chat_ids = _parse_chat_ids(_require(env, "ADBOT_SOURCE_CHAT_IDS"))
        source_ok = len(source_chat_ids) >= 1
        _print_result(source_ok, "ADBOT_SOURCE_CHAT_IDS", "must contain at least one numeric chat_id")
        if not source_ok:
            failures.append("ADBOT_SOURCE_CHAT_IDS")

        internal_chat_id = _require(env, "ADBOT_INTERNAL_CHAT_ID")
        internal_ok = bool(re.fullmatch(r"-?[0-9]+", internal_chat_id))
        _print_result(internal_ok, "ADBOT_INTERNAL_CHAT_ID", "must be a numeric chat_id")
        if not internal_ok:
            failures.append("ADBOT_INTERNAL_CHAT_ID")

    if failures:
        print("")
        print(f"FAIL: {len(failures)} check(s) failed: {', '.join(failures)}")
        print("Required external inputs usually are:")
        if pair_mode:
            print("- ADBOT_PAIR_COUNT + ADBOT_PAIR_<i>_* (source/internal/sensor/fallback contract)")
        else:
            print("- ADBOT_SOURCE_CHAT_IDS (prod source group chat ids)")
            print("- ADBOT_INTERNAL_CHAT_ID (prod internal audit group chat id)")
        if strict:
            raise SystemExit(1)
    else:
        print("")
        print("OK: prod adbot activation checklist passed.")


if __name__ == "__main__":
    main()
