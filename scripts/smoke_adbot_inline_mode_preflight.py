#!/usr/bin/env python3
"""
Preflight check for strict adbot E2E mode with real resident inline reply.

Contract:
- if ADBOT_E2E_ENABLED=0 -> skip (success);
- if ADBOT_E2E_ENABLED=1 and ADBOT_E2E_STRICT_REAL_INLINE!=1 -> skip (success);
- if strict mode enabled -> resident bot token must report supports_inline_queries=true.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import urllib.request


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


def _bot_get_me(token: str) -> dict | None:
    value = str(token or "").strip()
    if not value:
        return None
    url = f"https://api.telegram.org/bot{value}/getMe"
    with urllib.request.urlopen(url, timeout=10) as resp:  # nosec B310 - trusted Telegram API endpoint
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True, help="Path to .env file")
    args = parser.parse_args()

    env = _parse_env(Path(args.env_file))
    if not _is_true(env.get("ADBOT_E2E_ENABLED")):
        print("OK: adbot inline preflight skipped (ADBOT_E2E_ENABLED=0).")
        return
    if not _is_true(env.get("ADBOT_E2E_STRICT_REAL_INLINE")):
        print("OK: adbot inline preflight skipped (ADBOT_E2E_STRICT_REAL_INLINE!=1).")
        return

    token = str(env.get("BOT_TOKEN") or env.get("BOT_API_KEY") or "").strip()
    if not token:
        raise SystemExit("ERROR: strict adbot E2E requires BOT_TOKEN (or BOT_API_KEY) in env.")

    me = _bot_get_me(token)
    if not me:
        raise SystemExit("ERROR: failed to call getMe for resident bot token.")

    username = str(me.get("username") or "<unknown>")
    supports_inline = bool(me.get("supports_inline_queries"))
    if not supports_inline:
        raise SystemExit(
            "ERROR: strict adbot E2E requires resident inline mode enabled, "
            f"but @{username} reports supports_inline_queries=false. "
            "Enable inline mode in BotFather (/setinline) or set ADBOT_E2E_STRICT_REAL_INLINE=0."
        )

    print(f"OK: strict adbot E2E inline preflight passed for @{username}.")


if __name__ == "__main__":
    main()
