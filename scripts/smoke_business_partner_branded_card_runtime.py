#!/usr/bin/env python3
"""
Dynamic smoke test: Partner branded resident card runtime contract.

Validates:
- Partner place detail renders partner badge in resident card.
- Card keeps short description visible.
- Offer block ("Акції та офери") is rendered with partner offers.
- Partner branded photo buttons are present in detail keyboard.
- Gallery media callback (`pgm_<place_id>_<media_id>`) is present and opens media.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
import time
import types
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


def _setup_temp_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_partner_card__",))
        conn.commit()
    finally:
        conn.close()


def _collect_callbacks(reply_markup) -> list[str]:
    callbacks: list[str] = []
    if not reply_markup:
        return callbacks
    for row in getattr(reply_markup, "inline_keyboard", []):
        for button in row:
            cb = getattr(button, "callback_data", None)
            if cb:
                callbacks.append(str(cb))
    return callbacks


async def _run_checks() -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    from database import open_db  # noqa: WPS433
    import handlers as resident_handlers  # noqa: WPS433

    stamp = int(time.time())
    description = f"Короткий бренд-опис {stamp}"
    offer_1 = f"Знижка 10% до 12:00 {stamp}"
    offer_2 = f"Кава + круасан {stamp}"

    async with open_db() as db:
        async with db.execute(
            "SELECT id FROM general_services WHERE name = ? ORDER BY id DESC LIMIT 1",
            ("__smoke_partner_card__",),
        ) as cur:
            row = await cur.fetchone()
        _assert(row is not None, "smoke service is missing")
        service_id = int(row[0])

        cur = await db.execute(
            """
            INSERT INTO places(
                service_id, name, description, address, keywords, is_published,
                business_enabled, is_verified, verified_tier,
                offer_1_text, offer_2_text,
                photo_1_url, photo_2_url, photo_3_url
            ) VALUES(?, ?, ?, ?, ?, 1, 1, 1, 'partner', ?, ?, ?, ?, ?)
            """,
            (
                service_id,
                f"Partner Card Smoke {stamp}",
                description,
                "SMOKE address without map",
                "partner smoke",
                offer_1,
                offer_2,
                "https://example.org/p1.jpg",
                "https://example.org/p2.jpg",
                "https://example.org/p3.jpg",
            ),
        )
        place_id = int(cur.lastrowid)
        gallery_media_ref = "AgACAgIAAxkBAAIBQ6abcdefghijklmnoPQRSTUVWXYZ1234567890"
        gallery_cursor = await db.execute(
            """
            INSERT INTO place_gallery_media(place_id, media_ref, position, created_at, created_by)
            VALUES(?, ?, 1, datetime('now'), ?)
            """,
            (place_id, gallery_media_ref, 1),
        )
        gallery_media_id = int(gallery_cursor.lastrowid or 0)
        await db.commit()

    class _DummyMessage:
        def __init__(self) -> None:
            self.photo = None
            self.chat = SimpleNamespace(id=940001)
            self.message_id = 90
            self.edits: list[tuple[str, object]] = []
            self.answers: list[tuple[str, object]] = []
            self.deleted = False

        async def delete(self):
            self.deleted = True
            return True

        async def edit_text(self, text: str, reply_markup=None):
            self.edits.append((str(text), reply_markup))
            return SimpleNamespace(message_id=self.message_id)

        async def answer(self, text: str, reply_markup=None):
            self.answers.append((str(text), reply_markup))
            return SimpleNamespace(message_id=self.message_id + 1)

    msg = _DummyMessage()
    shown = await resident_handlers._render_place_detail_message(  # noqa: SLF001 - smoke targets internal render contract
        msg,
        place_id=place_id,
        user_id=940002,
    )
    _assert(bool(shown), "partner place detail was not rendered")

    rendered_text = ""
    rendered_markup = None
    if msg.edits:
        rendered_text, rendered_markup = msg.edits[-1]
    elif msg.answers:
        rendered_text, rendered_markup = msg.answers[-1]
    _assert(rendered_text, "no rendered text captured for partner card")

    _assert(
        "⭐ <b>Офіційний партнер категорії</b>" in rendered_text,
        f"partner badge missing in resident card:\n{rendered_text}",
    )
    _assert(
        f"📝 {description}" in rendered_text,
        f"partner short description missing in resident card:\n{rendered_text}",
    )
    _assert(
        "🎁 <b>Акції та офери:</b>" in rendered_text,
        f"partner offers block missing in resident card:\n{rendered_text}",
    )
    _assert(
        f"• {offer_1}" in rendered_text and f"• {offer_2}" in rendered_text,
        f"partner offers text missing in resident card:\n{rendered_text}",
    )

    callbacks = _collect_callbacks(rendered_markup)
    _assert(any(cb.startswith(f"pph1_{place_id}") for cb in callbacks), f"pph1 CTA missing: {callbacks}")
    _assert(any(cb.startswith(f"pph2_{place_id}") for cb in callbacks), f"pph2 CTA missing: {callbacks}")
    _assert(any(cb.startswith(f"pph3_{place_id}") for cb in callbacks), f"pph3 CTA missing: {callbacks}")
    gallery_cb = f"pgm_{place_id}_{gallery_media_id}"
    _assert(gallery_cb in callbacks, f"gallery CTA missing: {gallery_cb} in {callbacks}")

    opened_photos: list[str] = []
    safe_answers: list[dict] = []

    class _DummyOpenMessage:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(id=940001)
            self.message_id = 91
            self.bot = SimpleNamespace()

        async def answer_photo(self, photo, **_kwargs):
            opened_photos.append(str(photo))

        async def answer(self, *_args, **_kwargs):
            return None

    class _DummyCallback:
        def __init__(self, data: str, message: _DummyOpenMessage) -> None:
            self.data = str(data)
            self.message = message
            self.from_user = SimpleNamespace(id=940002, username="smoke_partner")

        async def answer(self, *args, **kwargs):
            safe_answers.append({"args": args, "kwargs": kwargs})

    original_safe_callback_answer = resident_handlers.safe_callback_answer

    async def _fake_safe_callback_answer(_callback, *args, **kwargs):
        safe_answers.append({"args": args, "kwargs": kwargs})
        return None

    resident_handlers.safe_callback_answer = _fake_safe_callback_answer
    try:
        await resident_handlers.cb_place_gallery_media_open(
            _DummyCallback(gallery_cb, _DummyOpenMessage())
        )
    finally:
        resident_handlers.safe_callback_answer = original_safe_callback_answer

    _assert(opened_photos == [gallery_media_ref], f"gallery open media mismatch: {opened_photos}")

    async with open_db() as db:
        async with db.execute(
            """
            SELECT COALESCE(SUM(cnt), 0)
              FROM place_clicks_daily
             WHERE place_id = ? AND action = 'gallery_open'
            """,
            (int(place_id),),
        ) as cur:
            row = await cur.fetchone()
            gallery_clicks = int(row[0] if row and row[0] is not None else 0)
    _assert(gallery_clicks == 1, f"gallery_open click counter mismatch: {gallery_clicks}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-partner-card-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: business partner branded card runtime smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
