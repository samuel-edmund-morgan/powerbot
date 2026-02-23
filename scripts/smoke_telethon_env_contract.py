#!/usr/bin/env python3
"""
Preflight env contract for Telethon-based test runners.

Purpose:
- fail fast with clear diagnostics when Telethon runners are enabled but env is not configured.
- avoid late, noisy failures in deploy_test after stack startup.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


_PLACEHOLDER_RE = re.compile(
    r"(your-|your_|your|placeholder|example|changeme|replace)",
    re.IGNORECASE,
)
_STRING_SESSION_RE = re.compile(r"^[A-Za-z0-9_=\-]+$")
_NUMERIC_CHAT_ID_RE = re.compile(r"^-?\d+$")


def _parse_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _is_true(raw: str | None) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_placeholder(raw: str) -> bool:
    value = str(raw or "").strip()
    if not value:
        return True
    return bool(_PLACEHOLDER_RE.search(value))


def _require(errors: list[str], env: dict[str, str], key: str, ctx: str) -> None:
    val = env.get(key, "")
    if not val:
        errors.append(f"{ctx}: missing `{key}`")


def _require_non_placeholder(
    errors: list[str], env: dict[str, str], key: str, ctx: str
) -> None:
    val = env.get(key, "")
    if _is_placeholder(val):
        errors.append(f"{ctx}: `{key}` is empty or placeholder")


def _looks_like_telethon_string_session(raw: str) -> bool:
    value = str(raw or "").strip()
    if not value:
        return False
    # Telethon StringSession is a long url-safe/base64-like token.
    # A short placeholder/token should be rejected early in preflight.
    if len(value) < 100:
        return False
    if not _STRING_SESSION_RE.fullmatch(value):
        return False
    return True


def _require_valid_string_session(
    errors: list[str], env: dict[str, str], key: str, ctx: str
) -> None:
    val = env.get(key, "")
    if _is_placeholder(val):
        errors.append(f"{ctx}: `{key}` is empty or placeholder")
        return
    if not _looks_like_telethon_string_session(val):
        errors.append(f"{ctx}: `{key}` does not look like a valid Telethon StringSession")


def _is_numeric_chat_id(raw: str) -> bool:
    return bool(_NUMERIC_CHAT_ID_RE.fullmatch(str(raw or "").strip()))


def _require_numeric_chat_id(
    errors: list[str], env: dict[str, str], key: str, ctx: str
) -> None:
    val = str(env.get(key, "")).strip()
    if not val:
        errors.append(f"{ctx}: missing `{key}`")
        return
    if not _is_numeric_chat_id(val):
        errors.append(f"{ctx}: `{key}` must be numeric chat_id")


def _require_numeric_chat_id_list(
    errors: list[str], env: dict[str, str], key: str, ctx: str
) -> None:
    val = str(env.get(key, "")).strip()
    if not val:
        errors.append(f"{ctx}: missing `{key}`")
        return
    tokens = [part.strip() for part in re.split(r"[,\s]+", val) if part.strip()]
    if not tokens:
        errors.append(f"{ctx}: `{key}` must contain at least one chat_id")
        return
    bad = [token for token in tokens if not _is_numeric_chat_id(token)]
    if bad:
        errors.append(f"{ctx}: `{key}` contains non-numeric chat_id values: {', '.join(bad)}")


def _same_non_empty(a: str | None, b: str | None) -> bool:
    left = str(a or "").strip()
    right = str(b or "").strip()
    return bool(left and right and left == right)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True, help="Path to .env file")
    args = parser.parse_args()

    env = _parse_env(Path(args.env_file))
    errors: list[str] = []

    if _is_true(env.get("TESTERBOT_ENABLED")):
        ctx = "testerbot"
        _require(errors, env, "TELETHON_API_ID", ctx)
        _require(errors, env, "TELETHON_API_HASH", ctx)
        _require_valid_string_session(errors, env, "TESTERBOT_STRING_SESSION", ctx)
        _require_non_placeholder(errors, env, "TESTERBOT_TARGET_POWERBOT_USERNAME", ctx)
        _require_non_placeholder(errors, env, "TESTERBOT_TARGET_ADMINBOT_USERNAME", ctx)
        _require_non_placeholder(errors, env, "TESTERBOT_TARGET_BUSINESSBOT_USERNAME", ctx)

    if _is_true(env.get("ADBOT_ENABLED")):
        ctx = "adbot"
        _require(errors, env, "TELETHON_API_ID", ctx)
        _require(errors, env, "TELETHON_API_HASH", ctx)
        _require_valid_string_session(errors, env, "ADBOT_STRING_SESSION", ctx)
        _require_non_placeholder(errors, env, "ADBOT_TARGET_POWERBOT_USERNAME", ctx)
        if not _is_true(env.get("ADBOT_TEST_MODE")):
            _require_numeric_chat_id_list(errors, env, "ADBOT_SOURCE_CHAT_IDS", ctx)
            _require_numeric_chat_id(errors, env, "ADBOT_INTERNAL_CHAT_ID", ctx)

    if _is_true(env.get("ADBOT_E2E_ENABLED")):
        ctx = "adbot_e2e"
        _require(errors, env, "TELETHON_API_ID", ctx)
        _require(errors, env, "TELETHON_API_HASH", ctx)
        _require_valid_string_session(errors, env, "ADBOT_E2E_DRIVER_STRING_SESSION", ctx)
        _require_numeric_chat_id(errors, env, "ADBOT_E2E_SOURCE_CHAT_ID", ctx)

    # If adbot and e2e-driver use the same Telethon account, require explicit safe mode.
    if _is_true(env.get("ADBOT_ENABLED")) and _is_true(env.get("ADBOT_E2E_ENABLED")):
        if _same_non_empty(env.get("ADBOT_STRING_SESSION"), env.get("ADBOT_E2E_DRIVER_STRING_SESSION")):
            if not _is_true(env.get("ADBOT_ALLOW_SELF_OUTGOING_E2E")):
                errors.append(
                    "adbot_e2e: ADBOT_E2E_DRIVER_STRING_SESSION equals ADBOT_STRING_SESSION; "
                    "set ADBOT_ALLOW_SELF_OUTGOING_E2E=1"
                )
            if not str(env.get("ADBOT_SELF_OUTGOING_PREFIX", "")).strip():
                errors.append(
                    "adbot_e2e: ADBOT_SELF_OUTGOING_PREFIX must be non-empty when sessions are identical"
                )
            if not str(env.get("ADBOT_E2E_PROMPT_PREFIX", "")).strip():
                errors.append(
                    "adbot_e2e: ADBOT_E2E_PROMPT_PREFIX must be non-empty when sessions are identical"
                )

    if errors:
        print("ERROR: Telethon env preflight failed:")
        for err in errors:
            print(f"- {err}")
        raise SystemExit(1)

    print("OK: Telethon env preflight passed.")


if __name__ == "__main__":
    main()
