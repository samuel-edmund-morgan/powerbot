#!/usr/bin/env python3
"""
Dynamic smoke test: business gallery runtime contract.

Validates:
- owner with active paid tier can add/list/remove gallery media;
- tier limit is enforced (Light: 6 items);
- resident card keyboard exposes `pgm_<place_id>_<media_id>` callbacks;
- gallery callback opens media and records `gallery_open` click.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
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

ADMIN_ID = 77
OWNER_ID = 9007
GALLERY_FILE_ID = "AgACAgIAAxkBAAIBQ5abcdefghijklmnoPQRSTUVWXYZ1234567890"


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _setup_temp_db(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_gallery__",))
        conn.execute(
            """
            INSERT INTO places(
                service_id, name, description, address, keywords,
                is_published, is_verified, verified_tier, business_enabled
            )
            VALUES(1, 'Gallery Smoke Place', 'Desc', 'Addr', 'gallery', 1, 0, NULL, 1)
            """
        )
        place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            """
            INSERT INTO business_owners(place_id, tg_user_id, role, status, created_at, approved_at, approved_by)
            VALUES(?, ?, 'owner', 'approved', datetime('now'), datetime('now'), ?)
            """,
            (place_id, OWNER_ID, ADMIN_ID),
        )
        conn.commit()
        return place_id
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


async def _run_checks(db_path: Path, *, place_id: int) -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    from business.repository import BusinessRepository  # noqa: WPS433
    from business.service import BusinessCabinetService, ValidationError  # noqa: WPS433
    from database import get_place_gallery_media  # noqa: WPS433
    import handlers as resident_handlers  # noqa: WPS433

    repo = BusinessRepository()
    service = BusinessCabinetService(repository=repo)

    paid = await service.change_subscription_tier(OWNER_ID, int(place_id), "light")
    _assert(str(paid.get("tier") or "") == "light", f"tier mismatch after activation: {paid}")

    # Light limit is 6 gallery items.
    added_ids: list[int] = []
    item1 = await service.add_place_gallery_media(
        tg_user_id=OWNER_ID,
        place_id=int(place_id),
        media_ref=GALLERY_FILE_ID,
    )
    added_ids.append(int(item1.get("id") or 0))
    _assert(added_ids[-1] > 0, f"gallery item id mismatch: {item1}")

    for idx in range(2, 7):
        item = await service.add_place_gallery_media(
            tg_user_id=OWNER_ID,
            place_id=int(place_id),
            media_ref=f"https://example.org/gallery-{idx}.jpg",
        )
        added_ids.append(int(item.get("id") or 0))
    _assert(len(added_ids) == 6, f"expected 6 items, got {added_ids}")

    try:
        await service.add_place_gallery_media(
            tg_user_id=OWNER_ID,
            place_id=int(place_id),
            media_ref="https://example.org/gallery-7.jpg",
        )
    except ValidationError as exc:
        _assert("ліміт галереї" in str(exc).lower(), f"unexpected limit error: {exc}")
    else:
        raise AssertionError("expected gallery limit ValidationError for Light tier")

    owner_list = await service.list_place_gallery_media(
        tg_user_id=OWNER_ID,
        place_id=int(place_id),
    )
    _assert(len(owner_list) == 6, f"owner gallery size mismatch: {owner_list}")

    public_list = await get_place_gallery_media(int(place_id), limit=20)
    _assert(len(public_list) == 6, f"public gallery size mismatch: {public_list}")

    place = await repo.get_place(int(place_id))
    _assert(place is not None, "place not found")
    kb = resident_handlers.build_place_detail_keyboard(
        dict(place),
        likes_count=0,
        user_liked=False,
        business_enabled=True,
        gallery_items=public_list,
    )
    callbacks = _collect_callbacks(kb)
    expected_cb = f"pgm_{int(place_id)}_{int(public_list[0]['id'])}"
    _assert(expected_cb in callbacks, f"gallery callback missing: {expected_cb} in {callbacks}")

    opened_photos: list[str] = []
    safe_answers: list[dict] = []

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
            safe_answers.append({"args": args, "kwargs": kwargs})

    original_safe_callback_answer = resident_handlers.safe_callback_answer

    async def _fake_safe_callback_answer(_callback, *args, **kwargs):
        safe_answers.append({"args": args, "kwargs": kwargs})
        return None

    resident_handlers.safe_callback_answer = _fake_safe_callback_answer
    try:
        message = _DummyMessage(chat_id=OWNER_ID)
        await resident_handlers.cb_place_gallery_media_open(_DummyCallback(expected_cb, message))
    finally:
        resident_handlers.safe_callback_answer = original_safe_callback_answer

    _assert(opened_photos == [GALLERY_FILE_ID], f"unexpected opened gallery media: {opened_photos}")

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(cnt), 0)
              FROM place_clicks_daily
             WHERE place_id = ? AND action = 'gallery_open'
            """,
            (int(place_id),),
        ).fetchone()
        clicks = int(row[0] if row and row[0] is not None else 0)
    finally:
        conn.close()
    _assert(clicks == 1, f"gallery_open click counter mismatch: {clicks}")

    await service.remove_place_gallery_media(
        tg_user_id=OWNER_ID,
        place_id=int(place_id),
        media_id=int(public_list[0]["id"]),
    )
    after_remove = await service.list_place_gallery_media(tg_user_id=OWNER_ID, place_id=int(place_id))
    _assert(len(after_remove) == 5, f"remove gallery item failed: {after_remove}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-gallery-runtime-"))
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
        print("OK: business gallery runtime smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()

