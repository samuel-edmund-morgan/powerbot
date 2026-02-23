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
        _require_non_placeholder(errors, env, "TESTERBOT_STRING_SESSION", ctx)
        _require_non_placeholder(errors, env, "TESTERBOT_TARGET_POWERBOT_USERNAME", ctx)
        _require_non_placeholder(errors, env, "TESTERBOT_TARGET_ADMINBOT_USERNAME", ctx)
        _require_non_placeholder(errors, env, "TESTERBOT_TARGET_BUSINESSBOT_USERNAME", ctx)

    if _is_true(env.get("ADBOT_ENABLED")):
        ctx = "adbot"
        _require(errors, env, "TELETHON_API_ID", ctx)
        _require(errors, env, "TELETHON_API_HASH", ctx)
        _require_non_placeholder(errors, env, "ADBOT_STRING_SESSION", ctx)
        _require_non_placeholder(errors, env, "ADBOT_TARGET_POWERBOT_USERNAME", ctx)
        if not _is_true(env.get("ADBOT_TEST_MODE")):
            _require_non_placeholder(errors, env, "ADBOT_SOURCE_CHAT_IDS", ctx)
            _require_non_placeholder(errors, env, "ADBOT_INTERNAL_CHAT_ID", ctx)

    if _is_true(env.get("ADBOT_E2E_ENABLED")):
        ctx = "adbot_e2e"
        _require(errors, env, "TELETHON_API_ID", ctx)
        _require(errors, env, "TELETHON_API_HASH", ctx)
        _require_non_placeholder(errors, env, "ADBOT_E2E_DRIVER_STRING_SESSION", ctx)
        _require(errors, env, "ADBOT_E2E_SOURCE_CHAT_ID", ctx)

    if errors:
        print("ERROR: Telethon env preflight failed:")
        for err in errors:
            print(f"- {err}")
        raise SystemExit(1)

    print("OK: Telethon env preflight passed.")


if __name__ == "__main__":
    main()
