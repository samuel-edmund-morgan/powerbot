#!/usr/bin/env python3
"""
Runtime smoke for resident `/adbot light_bind:<building>:<section>` path.

Runs against real handlers module inside powerbot container and verifies:
- light_bind query is parsed and routed to format_light_status with override ids;
- reply is sent back through adbot internal command channel.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _bootstrap_imports() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))


class _FakeMessage:
    def __init__(self, *, text: str, chat_id: int, user_id: int):
        self.text = text
        self.chat = SimpleNamespace(id=int(chat_id))
        self.from_user = SimpleNamespace(id=int(user_id))
        self.replies: list[str] = []
        self.answers: list[str] = []

    async def reply(self, text: str):
        self.replies.append(str(text))

    async def answer(self, text: str):
        self.answers.append(str(text))


async def _run() -> None:
    _bootstrap_imports()
    import handlers

    old_internal_chat_id = handlers.ADBOT_INTERNAL_CHAT_ID
    old_formatter = handlers.format_light_status
    captured: dict[str, int | bool] = {}
    try:
        handlers.ADBOT_INTERNAL_CHAT_ID = -100777001

        async def _fake_format_light_status(
            *,
            user_id: int,
            include_vote_prompt: bool = True,
            override_building_id: int | None = None,
            override_section_id: int | None = None,
            **_: object,
        ) -> str:
            captured["user_id"] = int(user_id)
            captured["include_vote_prompt"] = bool(include_vote_prompt)
            captured["building_id"] = int(override_building_id or 0)
            captured["section_id"] = int(override_section_id or 0)
            return f"bound:{override_building_id}:{override_section_id}"

        handlers.format_light_status = _fake_format_light_status

        msg = _FakeMessage(
            text="/adbot light_bind:1:2",
            chat_id=-100777001,
            user_id=900001,
        )
        await handlers.handle_adbot_internal_command(msg)

        _assert(captured.get("building_id") == 1, f"unexpected building override: {captured}")
        _assert(captured.get("section_id") == 2, f"unexpected section override: {captured}")
        _assert(
            captured.get("include_vote_prompt") is False,
            f"light bind must disable vote prompt: {captured}",
        )
        _assert(captured.get("user_id") == 900001, f"unexpected user id passthrough: {captured}")
        _assert(msg.replies == ["bound:1:2"], f"unexpected reply payload: replies={msg.replies} answers={msg.answers}")
        _assert(not msg.answers, "reply path should succeed without answer fallback")
    finally:
        handlers.ADBOT_INTERNAL_CHAT_ID = old_internal_chat_id
        handlers.format_light_status = old_formatter


def main() -> None:
    asyncio.run(_run())
    print("OK: adbot light_bind runtime smoke passed.")


if __name__ == "__main__":
    main()

