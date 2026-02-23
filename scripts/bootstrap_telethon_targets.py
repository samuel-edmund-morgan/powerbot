#!/usr/bin/env python3
"""
Bootstrap Telethon target usernames/IDs in .env from Telegram bot tokens.

This script is intentionally conservative:
- updates only empty/placeholder values;
- never overwrites explicitly configured non-placeholder values.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path


_PLACEHOLDER_RE = re.compile(
    r"(your-|your_|your|placeholder|example|changeme|replace|^$)",
    re.IGNORECASE,
)


def _is_placeholder(value: str | None) -> bool:
    return bool(_PLACEHOLDER_RE.search(str(value or "").strip()))


def _parse_env_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _parse_env_map(lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _set_env_key(lines: list[str], key: str, value: str) -> list[str]:
    rendered = f'{key}="{value}"' if not value.isdigit() else f"{key}={value}"
    out: list[str] = []
    changed = False
    for line in lines:
        if re.match(rf"^\s*{re.escape(key)}\s*=", line):
            out.append(rendered)
            changed = True
        else:
            out.append(line)
    if not changed:
        out.append(rendered)
    return out


def _bot_get_me(token: str) -> tuple[str, int] | None:
    token = str(token or "").strip()
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/getMe"
    with urllib.request.urlopen(url, timeout=10) as resp:  # nosec - controlled Telegram endpoint
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        return None
    result = payload.get("result") or {}
    username = str(result.get("username") or "").strip()
    bot_id = int(result.get("id") or 0)
    if not username or bot_id <= 0:
        return None
    return username, bot_id


def _maybe_update(
    lines: list[str], env: dict[str, str], key: str, value: str, *, updates: list[str]
) -> list[str]:
    current = env.get(key, "")
    if _is_placeholder(current):
        lines = _set_env_key(lines, key, value)
        env[key] = value
        updates.append(f"{key}=<auto>")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True, help="Path to .env file")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    lines = _parse_env_lines(env_path)
    env = _parse_env_map(lines)
    updates: list[str] = []

    discovered: dict[str, tuple[str, int]] = {}
    token_to_role = (
        ("BOT_TOKEN", "powerbot"),
        # Legacy alias kept for compatibility with older env templates.
        ("BOT_API_KEY", "powerbot"),
        ("ADMIN_BOT_API_KEY", "adminbot"),
        ("BUSINESS_BOT_API_KEY", "businessbot"),
    )
    for token_key, role in token_to_role:
        token = env.get(token_key, "")
        if role in discovered:
            # Keep the first successful discovery for each role.
            continue
        if not token:
            continue
        try:
            info = _bot_get_me(token)
        except Exception:
            info = None
        if info:
            discovered[role] = info

    if "powerbot" in discovered:
        username, _ = discovered["powerbot"]
        lines = _maybe_update(
            lines, env, "ADBOT_TARGET_POWERBOT_USERNAME", username, updates=updates
        )
        lines = _maybe_update(
            lines, env, "TESTERBOT_TARGET_POWERBOT_USERNAME", username, updates=updates
        )

    if "adminbot" in discovered:
        username, _ = discovered["adminbot"]
        lines = _maybe_update(
            lines, env, "TESTERBOT_TARGET_ADMINBOT_USERNAME", username, updates=updates
        )

    if "businessbot" in discovered:
        username, _ = discovered["businessbot"]
        lines = _maybe_update(
            lines, env, "TESTERBOT_TARGET_BUSINESSBOT_USERNAME", username, updates=updates
        )

    # Seed allowlist with discovered bot IDs if unset/placeholder.
    allow_key = "TESTERBOT_ALLOWED_CHAT_IDS"
    allow_current = env.get(allow_key, "")
    if _is_placeholder(allow_current):
        ids = [str(info[1]) for _, info in sorted(discovered.items()) if info[1] > 0]
        if ids:
            lines = _set_env_key(lines, allow_key, ",".join(ids))
            env[allow_key] = ",".join(ids)
            updates.append(f"{allow_key}=<auto:{len(ids)} ids>")

    if updates:
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("Updated Telethon targets:")
        for item in updates:
            print(f"- {item}")
    else:
        print("No Telethon target updates needed.")


if __name__ == "__main__":
    main()
