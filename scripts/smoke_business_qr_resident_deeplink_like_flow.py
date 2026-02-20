#!/usr/bin/env python3
"""
Dynamic smoke test: resident deep-link flow from business QR.

Validates:
- `/start place_<id>` renders the same resident place card flow as catalog:
  like button + report button + back-to-category callback.
- Like uniqueness contract still holds (one like per place per Telegram user).
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
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_qr_deeplink_like__",))
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

    from database import get_place_likes_count, like_place, open_db  # noqa: WPS433
    import handlers as resident_handlers  # noqa: WPS433

    stamp = int(time.time())
    user_id = 950000 + (stamp % 10000)

    async with open_db() as db:
        async with db.execute(
            "SELECT id FROM general_services WHERE name = ? ORDER BY id DESC LIMIT 1",
            ("__smoke_qr_deeplink_like__",),
        ) as cur:
            row = await cur.fetchone()
        _assert(row is not None, "smoke service missing")
        service_id = int(row[0])

        cur = await db.execute(
            """
            INSERT INTO places(
                service_id, name, description, address, keywords, is_published,
                business_enabled, is_verified, verified_tier, promo_code
            ) VALUES(?, ?, ?, ?, ?, 1, 1, 1, 'light', ?)
            """,
            (
                service_id,
                f"QR Deeplink Place {stamp}",
                "smoke deeplink description",
                "SMOKE address without map",
                "deeplink smoke",
                "DEEPLINK10",
            ),
        )
        place_id = int(cur.lastrowid)
        await db.commit()

    class _DummyMessage:
        def __init__(self, uid: int, pid: int):
            self.chat = SimpleNamespace(id=int(uid))
            self.from_user = SimpleNamespace(
                id=int(uid),
                username="deeplink_smoke",
                first_name="Deep",
                last_name="Link",
            )
            self.text = f"/start place_{int(pid)}"
            self.message_id = 111
            self.photo = None
            self.deleted = False
            self.answers: list[tuple[str, object]] = []
            self.answer_photos: list[tuple[str, object]] = []
            self.edits: list[tuple[str, object]] = []

        async def delete(self):
            self.deleted = True
            return True

        async def answer(self, text: str, reply_markup=None):
            self.answers.append((str(text), reply_markup))
            return SimpleNamespace(message_id=self.message_id + 1)

        async def answer_photo(self, photo=None, caption: str = "", reply_markup=None):
            self.answer_photos.append((str(caption), reply_markup))
            return SimpleNamespace(message_id=self.message_id + 2)

        async def edit_text(self, text: str, reply_markup=None):
            # Command message cannot be edited as bot-message in normal flow;
            # force fallback path to `answer(...)`.
            self.edits.append((str(text), reply_markup))
            raise RuntimeError("cannot edit command message")

    message = _DummyMessage(user_id, place_id)
    await resident_handlers.cmd_start(message)

    _assert(message.deleted, "cmd_start must try to delete incoming /start command")
    _assert(not message.answer_photos, "deeplink smoke uses address without map and should not send photo")
    _assert(message.answers, "deeplink flow must send place detail message")

    rendered_text, rendered_markup = message.answers[-1]
    callbacks = _collect_callbacks(rendered_markup)

    _assert(any(cb.startswith(f"like_{place_id}") for cb in callbacks), f"like button callback missing: {callbacks}")
    _assert(any(cb.startswith(f"plrep_{place_id}") for cb in callbacks), f"place-report callback missing: {callbacks}")
    _assert(
        any(cb == f"places_cat_{service_id}" for cb in callbacks),
        f"back-to-category callback missing: expected places_cat_{service_id}, got {callbacks}",
    )
    _assert(
        "Головне меню" not in rendered_text,
        "deeplink must render place-detail card, not legacy main-menu-only shortcut",
    )

    # One-like-per-user contract.
    first = await like_place(place_id, user_id)
    second = await like_place(place_id, user_id)
    likes = await get_place_likes_count(place_id)
    _assert(bool(first), "first like must be accepted")
    _assert(not bool(second), "second like from same user must be rejected")
    _assert(int(likes) == 1, f"likes count must stay 1 after duplicate like, got {likes}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-qr-deeplink-like-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: business QR resident deep-link like-flow smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
