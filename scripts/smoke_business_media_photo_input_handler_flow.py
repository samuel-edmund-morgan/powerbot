#!/usr/bin/env python3
"""
Dynamic smoke test: business owner media photo-input handler flow.

Validates:
- in `EditPlaceStates.waiting_value` media field accepts photo message input;
- handler stores Telegram `file_id` into place media field;
- state is cleared and success note is rendered.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


def _resolve_repo_root() -> Path:
    candidates: list[Path] = []
    try:
        candidates.append(Path(__file__).resolve().parents[1])
    except Exception:
        pass
    candidates.extend([Path.cwd(), Path("/app"), Path("/workspace")])
    for root in candidates:
        if (root / "schema.sql").exists() and (root / "src").exists():
            return root
    raise FileNotFoundError("Cannot locate repo root with schema.sql and src/")


REPO_ROOT = _resolve_repo_root()
SCHEMA_SQL = REPO_ROOT / "schema.sql"

ADMIN_ID = 42
OWNER_ID = 9001
PHOTO_FILE_ID = "AgACAgIAAxkBAAIBQ5abcdefghijklmnoPQRSTUVWXYZ1234567890"


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _setup_temp_db(db_path: Path) -> int:
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("Кав'ярні",))
        conn.execute(
            """
            INSERT INTO places(
                service_id, name, description, address, keywords,
                is_published, is_verified, verified_tier, verified_until, business_enabled
            ) VALUES(1, 'Photo Handler Place', 'Desc', 'Addr', 'photo', 1, 0, NULL, NULL, 1)
            """
        )
        place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            """
            INSERT INTO business_owners(place_id, tg_user_id, role, status, created_at, approved_at, approved_by)
            VALUES(?, ?, 'owner', 'approved', ?, ?, ?)
            """,
            (place_id, OWNER_ID, _iso(now), _iso(now), ADMIN_ID),
        )
        conn.commit()
        return place_id
    finally:
        conn.close()


async def _run_checks(place_id: int) -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    import business.handlers as bh  # noqa: WPS433

    sub = await bh.cabinet_service.change_subscription_tier(OWNER_ID, int(place_id), "partner")
    _assert(str(sub.get("tier") or "") == "partner", f"failed to activate partner tier: {sub}")

    class _FakeState:
        def __init__(self, *, pid: int, field: str) -> None:
            self._data = {"place_id": int(pid), "field": str(field)}
            self.cleared = False

        async def get_data(self):
            return dict(self._data)

        async def clear(self):
            self.cleared = True
            self._data = {}

    class _DummyMessage:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(id=int(OWNER_ID))
            self.from_user = SimpleNamespace(id=int(OWNER_ID), username="owner_smoke", first_name="Owner", last_name=None)
            self.bot = SimpleNamespace()
            self.photo = [SimpleNamespace(file_id=PHOTO_FILE_ID)]

        async def answer(self, *_args, **_kwargs):
            return None

        async def delete(self):
            return None

    state = _FakeState(pid=int(place_id), field="photo_1_url")
    message = _DummyMessage()

    render_calls: list[dict] = []
    ui_render_calls: list[dict] = []
    delete_calls: list[int] = []

    original_try_delete = bh.try_delete_user_message
    original_render_updated = bh.render_place_card_updated
    original_ui_render = bh.ui_render

    async def _fake_try_delete_user_message(msg):
        delete_calls.append(int(getattr(getattr(msg, "chat", None), "id", 0) or 0))

    async def _fake_render_place_card_updated(msg, *, place_id: int, note_text: str):
        render_calls.append(
            {
                "chat_id": int(msg.chat.id),
                "place_id": int(place_id),
                "note_text": str(note_text),
            }
        )

    async def _fake_ui_render(bot, *, chat_id: int, text: str, reply_markup=None, prefer_message_id=None, **kwargs):
        ui_render_calls.append(
            {
                "chat_id": int(chat_id),
                "text": str(text),
                "reply_markup": reply_markup,
                "prefer_message_id": prefer_message_id,
                "kwargs": kwargs,
            }
        )

    bh.try_delete_user_message = _fake_try_delete_user_message
    bh.render_place_card_updated = _fake_render_place_card_updated
    bh.ui_render = _fake_ui_render
    try:
        await bh.edit_place_apply_photo(message, state)
    finally:
        bh.try_delete_user_message = original_try_delete
        bh.render_place_card_updated = original_render_updated
        bh.ui_render = original_ui_render

    _assert(state.cleared, "FSM state must be cleared after successful photo apply")
    _assert(delete_calls == [OWNER_ID], f"user message should be deleted once: {delete_calls}")
    _assert(len(render_calls) == 1, f"success render_place_card_updated expected once: {render_calls}")
    _assert(not ui_render_calls, f"ui_render should not be used on success path: {ui_render_calls}")

    call = render_calls[0]
    _assert(call["place_id"] == int(place_id), f"rendered wrong place_id: {call}")
    _assert(call["note_text"] == "✅ Фото оновлено.", f"unexpected success note: {call}")

    place = await bh.cabinet_service.repository.get_place(int(place_id))
    _assert(str(place.get("photo_1_url") or "") == PHOTO_FILE_ID, f"photo file_id not saved: {place}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-media-photo-input-"))
    try:
        db_path = tmpdir / "state.db"
        place_id = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ["ADMIN_IDS"] = str(ADMIN_ID)
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks(int(place_id)))
        print("OK: business media photo-input handler flow smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
