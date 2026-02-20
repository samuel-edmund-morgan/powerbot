#!/usr/bin/env python3
"""
Dynamic smoke test: business media file_id runtime contract.

Checks:
- owner can save Telegram file_id into media fields (logo/offer/partner photo).
- resident place-card still exposes media CTA buttons for file_id values.
- media callbacks open photo via `answer_photo` (not URL redirect) and track clicks.
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

LOGO_FILE_ID = "AgACAgIAAxkBAAIBQ2abcdefghijklmnoPQRSTUVWXYZ1234567890"
OFFER_1_FILE_ID = "AgACAgIAAxkBAAIBQ3abcdefghijklmnoPQRSTUVWXYZ1234567890"
PARTNER_1_FILE_ID = "AgACAgIAAxkBAAIBQ4abcdefghijklmnoPQRSTUVWXYZ1234567890"


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
            ) VALUES(1, 'Media FileId Place', 'Desc', 'Addr', 'media', 1, 0, NULL, NULL, 1)
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


def _collect_callbacks(kb) -> list[str]:
    callbacks: list[str] = []
    for row in kb.inline_keyboard:
        for btn in row:
            cb = getattr(btn, "callback_data", None)
            if cb:
                callbacks.append(str(cb))
    return callbacks


async def _run_checks(db_path: Path, *, place_id: int) -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    from business.repository import BusinessRepository  # noqa: WPS433
    from business.service import BusinessCabinetService  # noqa: WPS433
    import handlers as resident_handlers  # noqa: WPS433

    repo = BusinessRepository()
    service = BusinessCabinetService(repository=repo)

    paid = await service.change_subscription_tier(OWNER_ID, int(place_id), "partner")
    _assert(str(paid.get("tier") or "") == "partner", f"tier mismatch after activation: {paid}")

    await service.update_place_business_profile_field(OWNER_ID, int(place_id), "logo_url", LOGO_FILE_ID)
    await service.update_place_business_profile_field(OWNER_ID, int(place_id), "offer_1_image_url", OFFER_1_FILE_ID)
    await service.update_place_business_profile_field(OWNER_ID, int(place_id), "photo_1_url", PARTNER_1_FILE_ID)

    place = await repo.get_place(int(place_id))
    _assert(str(place.get("logo_url") or "") == LOGO_FILE_ID, f"logo file_id mismatch: {place}")
    _assert(str(place.get("offer_1_image_url") or "") == OFFER_1_FILE_ID, f"offer_1 file_id mismatch: {place}")
    _assert(str(place.get("photo_1_url") or "") == PARTNER_1_FILE_ID, f"partner_1 file_id mismatch: {place}")

    kb = resident_handlers.build_place_detail_keyboard(
        place,
        likes_count=0,
        user_liked=False,
        business_enabled=True,
    )
    callbacks = _collect_callbacks(kb)
    _assert(f"plogo_{place_id}" in callbacks, f"logo callback missing for file_id media: {callbacks}")
    _assert(f"pmimg1_{place_id}" in callbacks, f"offer image callback missing for file_id media: {callbacks}")
    _assert(f"pph1_{place_id}" in callbacks, f"partner photo callback missing for file_id media: {callbacks}")

    opened_photos: list[str] = []
    safe_answers: list[tuple[str, tuple, dict]] = []

    class _DummyMessage:
        def __init__(self, chat_id: int) -> None:
            self.chat = SimpleNamespace(id=int(chat_id))
            self.message_id = 77
            self.bot = SimpleNamespace()

        async def answer_photo(self, photo, **_kwargs) -> None:
            opened_photos.append(str(photo))

        async def answer(self, *_args, **_kwargs) -> None:
            return None

    class _DummyCallback:
        def __init__(self, data: str, message: _DummyMessage) -> None:
            self.data = str(data)
            self.message = message
            self.from_user = SimpleNamespace(id=OWNER_ID)

        async def answer(self, *args, **kwargs) -> None:
            safe_answers.append((self.data, args, kwargs))

    original_safe_callback_answer = resident_handlers.safe_callback_answer

    async def _fake_safe_callback_answer(callback, *args, **kwargs):
        safe_answers.append((str(getattr(callback, "data", "")), args, kwargs))
        return None

    resident_handlers.safe_callback_answer = _fake_safe_callback_answer
    try:
        message = _DummyMessage(chat_id=OWNER_ID)
        await resident_handlers.cb_place_logo_open(_DummyCallback(f"plogo_{place_id}", message))
        await resident_handlers.cb_place_offer_1_image_open(_DummyCallback(f"pmimg1_{place_id}", message))
        await resident_handlers.cb_place_partner_photo_1_open(_DummyCallback(f"pph1_{place_id}", message))
    finally:
        resident_handlers.safe_callback_answer = original_safe_callback_answer

    _assert(
        opened_photos == [LOGO_FILE_ID, OFFER_1_FILE_ID, PARTNER_1_FILE_ID],
        f"unexpected opened file_ids: {opened_photos}",
    )
    media_callbacks = {
        f"plogo_{place_id}",
        f"pmimg1_{place_id}",
        f"pph1_{place_id}",
    }
    media_safe_answers = [entry for entry in safe_answers if str(entry[0]) in media_callbacks]
    _assert(media_safe_answers, f"no callback answers captured for media callbacks: {safe_answers}")
    for data, _args, kwargs in media_safe_answers:
        _assert(
            "url" not in kwargs or kwargs.get("url") in (None, ""),
            f"file_id media callback must not use URL redirect path: data={data} kwargs={kwargs}",
        )

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT action, cnt
              FROM place_clicks_daily
             WHERE place_id = ?
               AND action IN ('logo_open', 'offer1_image', 'partner_photo_1')
            """,
            (int(place_id),),
        ).fetchall()
    finally:
        conn.close()

    counters = {str(action): int(cnt or 0) for action, cnt in rows}
    _assert(counters.get("logo_open") == 1, f"logo_open counter mismatch: {counters}")
    _assert(counters.get("offer1_image") == 1, f"offer1_image counter mismatch: {counters}")
    _assert(counters.get("partner_photo_1") == 1, f"partner_photo_1 counter mismatch: {counters}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-media-fileid-"))
    try:
        db_path = tmpdir / "state.db"
        place_id = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ["ADMIN_IDS"] = str(ADMIN_ID)
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks(db_path, place_id=int(place_id)))
        print("OK: business media file_id runtime smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
