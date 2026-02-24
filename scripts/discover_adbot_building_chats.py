#!/usr/bin/env python3
"""
Discover Telegram chats for building names (Telethon session based).

Outputs anonymized chat metadata for manual review:
- chat_id
- chat title
- username (if any)
- matched building keywords
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path


DEFAULT_BUILDING_KEYWORDS = (
    "ньюкасл",
    "брістоль",
    "ліверпуль",
    "ноттінгем",
    "манчестер",
    "кембрідж",
    "брайтон",
    "бермінгем",
    "віндзор",
    "честер",
    "лондон",
    "оксфорд",
    "лінкольн",
    "престон",
)


def _strip_quotes(value: str | None) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _parse_keywords(raw: str | None) -> tuple[str, ...]:
    value = _strip_quotes(raw)
    if not value:
        return DEFAULT_BUILDING_KEYWORDS
    parts = [token.strip().casefold() for token in value.split(",")]
    return tuple(token for token in parts if token)


async def _run(args: argparse.Namespace) -> list[dict]:
    from telethon import TelegramClient  # type: ignore
    from telethon.sessions import StringSession  # type: ignore

    api_id = int(_strip_quotes(args.api_id))
    api_hash = _strip_quotes(args.api_hash)
    session = _strip_quotes(args.session)
    keywords = _parse_keywords(args.keywords)

    if api_id <= 0 or not api_hash or not session:
        raise SystemExit("ERROR: TELETHON_API_ID/TELETHON_API_HASH/SESSION are required")

    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise SystemExit("ERROR: provided session is not authorized")

    discovered: list[dict] = []
    try:
        async for dialog in client.iter_dialogs():
            title = str(getattr(dialog, "name", "") or "").strip()
            if not title:
                continue
            lowered = title.casefold()
            matched = [kw for kw in keywords if kw in lowered]
            if not matched:
                continue
            entity = dialog.entity
            discovered.append(
                {
                    "chat_id": int(getattr(entity, "id", 0) or 0),
                    "title": title,
                    "username": str(getattr(entity, "username", "") or ""),
                    "matched_keywords": matched,
                }
            )
    finally:
        await client.disconnect()

    discovered.sort(key=lambda row: (row["title"].casefold(), row["chat_id"]))
    return discovered


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover building chats for adbot mining")
    parser.add_argument("--api-id", default=os.getenv("TELETHON_API_ID", "0"))
    parser.add_argument("--api-hash", default=os.getenv("TELETHON_API_HASH", ""))
    parser.add_argument("--session", default=os.getenv("ADBOT_STRING_SESSION", ""))
    parser.add_argument(
        "--keywords",
        default=",".join(DEFAULT_BUILDING_KEYWORDS),
        help="Comma-separated building keywords to match in chat titles",
    )
    parser.add_argument("--output-json", default="/tmp/adbot_building_chats.json")
    args = parser.parse_args()

    rows = asyncio.run(_run(args))
    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"chats": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OK: discovered {len(rows)} chats -> {output_path}")


if __name__ == "__main__":
    main()

