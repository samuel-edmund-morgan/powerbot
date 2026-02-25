#!/usr/bin/env python3
"""
Runtime smoke for adbot pair-mode light routing (sensor_uuid -> fallback).

Runs inside runtime image and validates that listener resolves light query
per pair using active sensor mapping first, then fallback building/section.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
from dataclasses import dataclass


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _bootstrap_imports() -> None:
    from pathlib import Path

    candidates = [Path.cwd()]
    raw_file = globals().get("__file__")
    if isinstance(raw_file, str) and raw_file and raw_file != "<stdin>":
        try:
            candidates.append(Path(raw_file).resolve().parents[1])
        except Exception:
            pass

    for base in candidates:
        src_root = base / "src"
        if src_root.exists() and str(src_root) not in sys.path:
            sys.path.insert(0, str(src_root))


@dataclass
class _FakeMessage:
    text: str
    id: int
    sender_id: int = 1001
    out: bool = False


class _FakeEvent:
    def __init__(self, *, text: str, chat_id: int, msg_id: int):
        self.chat_id = int(chat_id)
        self.client = self
        self.message = _FakeMessage(text=text, id=int(msg_id))
        self.responses: list[tuple[str, int | None]] = []

    async def respond(self, text: str, reply_to: int | None = None):
        self.responses.append((str(text), reply_to))

    async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
        if from_peer is None:
            return [type("FwdMsg", (), {"id": 700001})()]
        return [type("FwdMsg", (), {"id": 800001, "fwd_from": object()})()]

    async def send_message(self, chat_id: int, text: str):
        return type("Msg", (), {"id": 900001})()


class _FakePipeline:
    async def answer(self, query: str, fallback: str) -> str:
        return fallback


class _CaptureInternalPipeline:
    def __init__(self):
        self.calls: list[tuple[str, int, int | None]] = []

    async def get_via_internal(
        self,
        *,
        query: str,
        fallback: str,
        internal_chat_id: int,
        reply_to_message_id: int | None = None,
    ):
        from adbot.pipeline import InternalReplyResult

        self.calls.append((str(query), int(internal_chat_id), reply_to_message_id))
        return InternalReplyResult(
            text=f"ok:{query}:{internal_chat_id}",
            reason=None,
            internal_message_id=123456,
            via_bot_id=654321,
        )


async def _run() -> None:
    _bootstrap_imports()
    from adbot.cooldown import CooldownGuard
    from adbot.listener import AdbotListener, AdbotRuntimePair

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp_db:
        db_path = tmp_db.name
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE sensors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uuid TEXT,
                    building_id INTEGER,
                    section_id INTEGER,
                    is_active INTEGER
                )
                """
            )
            conn.execute(
                "INSERT INTO sensors(uuid, building_id, section_id, is_active) VALUES(?, ?, ?, ?)",
                ("esp32-newcastle-001", 1, 2, 1),
            )
            conn.execute(
                "INSERT INTO sensors(uuid, building_id, section_id, is_active) VALUES(?, ?, ?, ?)",
                ("esp32-oxford-001", 12, 1, 0),
            )
            conn.commit()
        finally:
            conn.close()

        old_db_path = os.environ.get("DB_PATH")
        os.environ["DB_PATH"] = db_path
        try:
            internal = _CaptureInternalPipeline()
            listener = AdbotListener(
                matcher_min_len=10,
                matcher_max_len=280,
                matcher_min_confidence=120,
                cooldown=CooldownGuard(10800),
                pipeline=_FakePipeline(),
                internal_pipeline=internal,
                internal_chat_id=None,
                chat_pairs=(
                    AdbotRuntimePair(
                        idx=1,
                        source_chat_id=-1007001,
                        internal_chat_id=-1008001,
                        sensor_uuid="esp32-newcastle-001",
                        fallback_building_id=9,
                        fallback_section_id=9,
                        reply_cooldown_sec=10800,
                        label="newcastle",
                    ),
                    AdbotRuntimePair(
                        idx=2,
                        source_chat_id=-1007002,
                        internal_chat_id=-1008002,
                        sensor_uuid="esp32-oxford-001",
                        fallback_building_id=12,
                        fallback_section_id=1,
                        reply_cooldown_sec=0,
                        label="oxford",
                    ),
                ),
                require_real_internal_reply=False,
            )

            evt_sensor = _FakeEvent(text="Чи є світло?", chat_id=-1007001, msg_id=1)
            _assert(await listener.process(evt_sensor, source_chat_id=evt_sensor.chat_id), "sensor pair must handle")

            evt_fallback = _FakeEvent(text="Є світло?", chat_id=-1007002, msg_id=2)
            _assert(await listener.process(evt_fallback, source_chat_id=evt_fallback.chat_id), "fallback pair must handle")

            _assert(
                ("light_bind:1:2", -1008001, 700001) in internal.calls,
                f"sensor route not applied: calls={internal.calls}",
            )
            _assert(
                ("light_bind:12:1", -1008002, 700001) in internal.calls,
                f"fallback route not applied: calls={internal.calls}",
            )
        finally:
            if old_db_path is None:
                os.environ.pop("DB_PATH", None)
            else:
                os.environ["DB_PATH"] = old_db_path


def main() -> None:
    asyncio.run(_run())
    print("OK: adbot pair light runtime smoke passed.")


if __name__ == "__main__":
    main()
