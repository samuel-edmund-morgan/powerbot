#!/usr/bin/env python3
"""
Best-effort bootstrap for adbot chat IDs in .env using Telethon dialogs.

Updates only placeholder/empty values and never overwrites explicit IDs.
Intended for test deploy flow where chat titles are known beforehand.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path


_PLACEHOLDER_RE = re.compile(
    r"(your-|your_|your|placeholder|example|changeme|replace|^$)",
    re.IGNORECASE,
)

DEFAULT_SOURCE_TITLE = "Тестовий груповий чат"
DEFAULT_INTERNAL_TITLE = "Тестова внутрішня група"


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
    rendered = f'{key}="{value}"' if re.search(r"\s", value) else f"{key}={value}"
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


def _parse_title_list(raw: str) -> tuple[str, ...]:
    tokens = [t.strip() for t in re.split(r"[,\n]+", str(raw or "")) if t.strip()]
    return tuple(dict.fromkeys(tokens))


def _select_session(env: dict[str, str]) -> str:
    for key in (
        "ADBOT_E2E_DRIVER_STRING_SESSION",
        "ADBOT_STRING_SESSION",
        "TESTERBOT_STRING_SESSION",
    ):
        value = str(env.get(key, "")).strip()
        if value and not _is_placeholder(value):
            return value
    return ""


async def _resolve_titles_to_ids(session: str, api_id: int, api_hash: str, titles: tuple[str, ...]) -> dict[str, int]:
    from telethon import TelegramClient  # type: ignore
    from telethon.sessions import StringSession  # type: ignore

    requested = {title.casefold(): title for title in titles}
    resolved: dict[str, int] = {}

    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return {}

        async for dialog in client.iter_dialogs():
            title = str(getattr(dialog, "name", "") or "").strip()
            if not title:
                continue
            key = title.casefold()
            if key not in requested:
                continue
            if requested[key] in resolved:
                continue
            resolved[requested[key]] = int(getattr(dialog, "id", 0) or 0)
    finally:
        await client.disconnect()
    return {k: v for k, v in resolved.items() if int(v) != 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True, help="Path to .env file")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    lines = _parse_env_lines(env_path)
    env = _parse_env_map(lines)
    updates: list[str] = []

    try:
        api_id = int(str(env.get("TELETHON_API_ID", "")).strip() or "0")
    except Exception:
        api_id = 0
    api_hash = str(env.get("TELETHON_API_HASH", "")).strip()
    session = _select_session(env)
    if api_id <= 0 or not api_hash or not session:
        print("No adbot chat-id bootstrap needed (missing TELETHON creds/session).")
        return

    source_titles = _parse_title_list(env.get("ADBOT_SOURCE_CHAT_TITLES", "") or DEFAULT_SOURCE_TITLE)
    internal_title = str(env.get("ADBOT_INTERNAL_CHAT_TITLE", "")).strip() or DEFAULT_INTERNAL_TITLE
    e2e_source_title = str(env.get("ADBOT_E2E_SOURCE_CHAT_TITLE", "")).strip() or DEFAULT_SOURCE_TITLE
    e2e_internal_title = str(env.get("ADBOT_E2E_INTERNAL_CHAT_TITLE", "")).strip() or DEFAULT_INTERNAL_TITLE
    try:
        pair_count = max(int(str(env.get("ADBOT_PAIR_COUNT", "")).strip() or "0"), 0)
    except Exception:
        pair_count = 0
    pair_title_keys: list[tuple[str, str]] = []
    for idx in range(1, pair_count + 1):
        src_title_key = f"ADBOT_PAIR_{idx}_SOURCE_CHAT_TITLE"
        int_title_key = f"ADBOT_PAIR_{idx}_INTERNAL_CHAT_TITLE"
        src_title = str(env.get(src_title_key, "")).strip()
        int_title = str(env.get(int_title_key, "")).strip()
        if src_title:
            pair_title_keys.append((f"ADBOT_PAIR_{idx}_SOURCE_CHAT_ID", src_title))
        if int_title:
            pair_title_keys.append((f"ADBOT_PAIR_{idx}_INTERNAL_CHAT_ID", int_title))

    all_titles = tuple(
        dict.fromkeys(
            [
                *source_titles,
                internal_title,
                e2e_source_title,
                e2e_internal_title,
                *[title for _, title in pair_title_keys],
            ]
        )
    )
    try:
        resolved = asyncio.run(_resolve_titles_to_ids(session, api_id, api_hash, all_titles))
    except Exception as exc:
        print(f"Adbot chat-id bootstrap skipped: {exc.__class__.__name__}: {exc}")
        return

    if _is_placeholder(env.get("ADBOT_SOURCE_CHAT_IDS", "")) and source_titles:
        ids = [str(resolved.get(title, "")) for title in source_titles]
        ids = [x for x in ids if x and re.fullmatch(r"-?\d+", x)]
        if ids:
            value = ",".join(dict.fromkeys(ids))
            lines = _set_env_key(lines, "ADBOT_SOURCE_CHAT_IDS", value)
            env["ADBOT_SOURCE_CHAT_IDS"] = value
            updates.append("ADBOT_SOURCE_CHAT_IDS=<auto>")

    if _is_placeholder(env.get("ADBOT_INTERNAL_CHAT_ID", "")):
        internal_id = str(resolved.get(internal_title, "")).strip()
        if re.fullmatch(r"-?\d+", internal_id):
            lines = _set_env_key(lines, "ADBOT_INTERNAL_CHAT_ID", internal_id)
            env["ADBOT_INTERNAL_CHAT_ID"] = internal_id
            updates.append("ADBOT_INTERNAL_CHAT_ID=<auto>")

    if _is_placeholder(env.get("ADBOT_E2E_SOURCE_CHAT_ID", "")):
        e2e_source_id = str(resolved.get(e2e_source_title, "")).strip()
        if re.fullmatch(r"-?\d+", e2e_source_id):
            lines = _set_env_key(lines, "ADBOT_E2E_SOURCE_CHAT_ID", e2e_source_id)
            env["ADBOT_E2E_SOURCE_CHAT_ID"] = e2e_source_id
            updates.append("ADBOT_E2E_SOURCE_CHAT_ID=<auto>")

    if _is_placeholder(env.get("ADBOT_E2E_INTERNAL_CHAT_ID", "")):
        e2e_internal_id = str(resolved.get(e2e_internal_title, "")).strip()
        if re.fullmatch(r"-?\d+", e2e_internal_id):
            lines = _set_env_key(lines, "ADBOT_E2E_INTERNAL_CHAT_ID", e2e_internal_id)
            env["ADBOT_E2E_INTERNAL_CHAT_ID"] = e2e_internal_id
            updates.append("ADBOT_E2E_INTERNAL_CHAT_ID=<auto>")

    for target_key, title in pair_title_keys:
        if not _is_placeholder(env.get(target_key, "")):
            continue
        resolved_id = str(resolved.get(title, "")).strip()
        if re.fullmatch(r"-?\d+", resolved_id):
            lines = _set_env_key(lines, target_key, resolved_id)
            env[target_key] = resolved_id
            updates.append(f"{target_key}=<auto>")

    if updates:
        try:
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except PermissionError:
            print(
                "Adbot chat-id bootstrap: resolved values but env file is read-only; "
                "skipping write."
            )
            return
        print("Updated adbot chat ids:")
        for item in updates:
            print(f"- {item}")
    else:
        print("No adbot chat-id updates needed.")


if __name__ == "__main__":
    main()
