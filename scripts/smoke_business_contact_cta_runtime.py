#!/usr/bin/env python3
"""
Dynamic smoke test: resident contact CTA callbacks runtime contract.

Validates:
- `pchat_` callback redirects to normalized `t.me` URL and records `chat` click.
- `pcall_` callback redirects to normalized `tel:` URL and records `call` click.
- invalid call contact does not record click and returns validation alert.
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


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _setup_temp_db(db_path: Path) -> tuple[int, int]:
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_contact_cta__",))
        service_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            """
            INSERT INTO places(
                service_id, name, description, address, keywords,
                is_published, business_enabled, is_verified, verified_tier, verified_until,
                contact_type, contact_value
            ) VALUES(?, 'Contact CTA Smoke', 'smoke', 'addr', 'kw', 1, 1, 1, 'light', ?, 'chat', '@smoke_chat')
            """,
            (service_id, now_iso),
        )
        place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.commit()
        return service_id, place_id
    finally:
        conn.close()


async def _get_click_sum(place_id: int, action: str) -> int:
    from database import open_db  # noqa: WPS433

    async with open_db() as db:
        async with db.execute(
            """
            SELECT COALESCE(SUM(cnt), 0)
              FROM place_clicks_daily
             WHERE place_id = ? AND action = ?
            """,
            (int(place_id), str(action)),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0] if row and row[0] is not None else 0)


async def _run_checks(place_id: int) -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    from database import open_db  # noqa: WPS433
    import handlers as resident_handlers  # noqa: WPS433

    safe_calls: list[dict] = []

    class _DummyMessage:
        def __init__(self) -> None:
            self.answers: list[dict] = []

        async def answer(self, text: str, reply_markup=None):
            self.answers.append({"text": str(text), "reply_markup": reply_markup})
            return None

    class _DummyCallback:
        def __init__(self, data: str, message: _DummyMessage):
            self.data = data
            self.message = message
            self.from_user = SimpleNamespace(id=950001, username="contact_smoke", first_name="Contact")

        async def answer(self, *_args, **_kwargs):
            return True

    original_safe_callback_answer = resident_handlers.safe_callback_answer

    async def _fake_safe_callback_answer(callback, text=None, **kwargs):
        safe_calls.append(
            {
                "text": None if text is None else str(text),
                "kwargs": dict(kwargs),
            }
        )
        return True

    resident_handlers.safe_callback_answer = _fake_safe_callback_answer
    try:
        # 1) Chat CTA success.
        msg_chat = _DummyMessage()
        cb_chat = _DummyCallback(f"pchat_{int(place_id)}", msg_chat)
        before_chat = await _get_click_sum(int(place_id), "chat")
        await resident_handlers.cb_place_chat_open(cb_chat)
        after_chat = await _get_click_sum(int(place_id), "chat")
        _assert(after_chat == before_chat + 1, f"chat click counter mismatch: before={before_chat}, after={after_chat}")
        _assert(len(msg_chat.answers) == 0, f"chat success should not use message.answer fallback: {msg_chat.answers}")
        _assert(safe_calls, "safe_callback_answer calls missing for chat path")
        _assert(
            str(safe_calls[-1]["kwargs"].get("url") or "") == "https://t.me/smoke_chat",
            f"chat redirect URL mismatch: {safe_calls[-1]}",
        )

        # 2) Call CTA success.
        async with open_db() as db:
            await db.execute(
                "UPDATE places SET contact_type = 'call', contact_value = '+380 67 111 22 33' WHERE id = ?",
                (int(place_id),),
            )
            await db.commit()

        msg_call = _DummyMessage()
        cb_call = _DummyCallback(f"pcall_{int(place_id)}", msg_call)
        before_call = await _get_click_sum(int(place_id), "call")
        await resident_handlers.cb_place_call_open(cb_call)
        after_call = await _get_click_sum(int(place_id), "call")
        _assert(after_call == before_call + 1, f"call click counter mismatch: before={before_call}, after={after_call}")
        _assert(len(msg_call.answers) == 0, f"call success should not use message.answer fallback: {msg_call.answers}")
        _assert(
            str(safe_calls[-1]["kwargs"].get("url") or "") == "tel:+380671112233",
            f"call redirect URL mismatch: {safe_calls[-1]}",
        )

        # 3) Invalid call contact -> alert and no additional click increment.
        async with open_db() as db:
            await db.execute(
                "UPDATE places SET contact_type = 'call', contact_value = 'abc' WHERE id = ?",
                (int(place_id),),
            )
            await db.commit()

        msg_bad = _DummyMessage()
        cb_bad = _DummyCallback(f"pcall_{int(place_id)}", msg_bad)
        before_bad = await _get_click_sum(int(place_id), "call")
        await resident_handlers.cb_place_call_open(cb_bad)
        after_bad = await _get_click_sum(int(place_id), "call")
        _assert(after_bad == before_bad, f"invalid call contact must not increment clicks: {before_bad}->{after_bad}")
        _assert(len(msg_bad.answers) == 0, f"invalid call contact should not send tel-button message: {msg_bad.answers}")
        _assert(
            str(safe_calls[-1]["text"] or "") == "Некоректний номер телефону.",
            f"invalid call should return validation alert text: {safe_calls[-1]}",
        )
        _assert(bool(safe_calls[-1]["kwargs"].get("show_alert")), f"invalid call should use alert: {safe_calls[-1]}")
    finally:
        resident_handlers.safe_callback_answer = original_safe_callback_answer


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-contact-cta-runtime-"))
    try:
        db_path = tmpdir / "state.db"
        _, place_id = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ["BUSINESS_MODE"] = "1"
        os.environ.setdefault("ADMIN_IDS", "1")

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks(int(place_id)))
        print("OK: business contact CTA runtime smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
