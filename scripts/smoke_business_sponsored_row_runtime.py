#!/usr/bin/env python3
"""
Dynamic smoke test: sponsored row in resident main menu.

Validates:
- Partner place can be shown as sponsored row in main menu (once per day per user).
- User can disable/enable sponsored offers via notifications setting.
- Notifications keyboard reflects sponsored toggle status.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
import time
import types
from datetime import datetime, timedelta
from pathlib import Path


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
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_sponsored_row__",))
        conn.commit()
    finally:
        conn.close()


def _callbacks(reply_markup) -> list[str]:
    result: list[str] = []
    if not reply_markup:
        return result
    for row in getattr(reply_markup, "inline_keyboard", []):
        for btn in row:
            cb = getattr(btn, "callback_data", None)
            if cb:
                result.append(str(cb))
    return result


def _button_text_by_callback(reply_markup, callback_data: str) -> str:
    if not reply_markup:
        return ""
    for row in getattr(reply_markup, "inline_keyboard", []):
        for btn in row:
            if str(getattr(btn, "callback_data", "")) == callback_data:
                return str(getattr(btn, "text", "") or "")
    return ""


async def _run_checks() -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    from database import db_set, open_db  # noqa: WPS433
    import handlers as resident_handlers  # noqa: WPS433

    stamp = int(time.time())
    chat_id = 960000 + (stamp % 10000)

    async with open_db() as db:
        async with db.execute(
            "SELECT id FROM general_services WHERE name = ? ORDER BY id DESC LIMIT 1",
            ("__smoke_sponsored_row__",),
        ) as cur:
            row = await cur.fetchone()
        _assert(row is not None, "smoke service missing")
        service_id = int(row[0])

        cur = await db.execute(
            """
            INSERT INTO places(
                service_id, name, description, address, keywords, is_published,
                business_enabled, is_verified, verified_tier
            ) VALUES(?, ?, ?, ?, ?, 1, 1, 1, 'partner')
            """,
            (
                service_id,
                f"Partner Sponsor {stamp}",
                "smoke sponsor place",
                "SMOKE sponsored address",
                "sponsor smoke",
            ),
        )
        place_id = int(cur.lastrowid)
        await db.commit()

    # 1) First main-menu render: sponsored row should appear.
    kb_first = await resident_handlers.get_main_keyboard_for_user(chat_id)
    first_callbacks = _callbacks(kb_first)
    _assert(
        any(cb == f"place_{place_id}" for cb in first_callbacks),
        f"sponsored row callback missing on first render: {first_callbacks}",
    )

    # 2) Second render same day: row should not appear again.
    kb_second = await resident_handlers.get_main_keyboard_for_user(chat_id)
    second_callbacks = _callbacks(kb_second)
    _assert(
        not any(cb == f"place_{place_id}" for cb in second_callbacks),
        f"sponsored row must be throttled to once/day: {second_callbacks}",
    )

    # 3) Notifications menu should contain sponsored toggle and be ON by default.
    notif_kb_on = await resident_handlers.get_notifications_keyboard(chat_id)
    toggle_text_on = _button_text_by_callback(notif_kb_on, "notif_toggle_sponsored")
    _assert(bool(toggle_text_on), "notifications keyboard missing `notif_toggle_sponsored` button")
    _assert("✅" in toggle_text_on, f"sponsored toggle must be ON by default, got: {toggle_text_on}")

    # 4) Disable sponsored offers -> menu should hide row.
    await resident_handlers._set_sponsored_offers_enabled(chat_id, False)  # noqa: SLF001 - runtime smoke of internal contract
    await db_set(resident_handlers._sponsored_last_seen_day_key(chat_id), "")  # noqa: SLF001 - reset throttle for deterministic check
    kb_disabled = await resident_handlers.get_main_keyboard_for_user(chat_id)
    disabled_callbacks = _callbacks(kb_disabled)
    _assert(
        not any(cb == f"place_{place_id}" for cb in disabled_callbacks),
        f"sponsored row must be hidden when disabled: {disabled_callbacks}",
    )
    notif_kb_off = await resident_handlers.get_notifications_keyboard(chat_id)
    toggle_text_off = _button_text_by_callback(notif_kb_off, "notif_toggle_sponsored")
    _assert("❌" in toggle_text_off, f"sponsored toggle must show OFF status, got: {toggle_text_off}")

    # 5) Enable back and rewind throttle day -> row should appear again.
    await resident_handlers._set_sponsored_offers_enabled(chat_id, True)  # noqa: SLF001
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    await db_set(resident_handlers._sponsored_last_seen_day_key(chat_id), yesterday)  # noqa: SLF001
    kb_enabled_again = await resident_handlers.get_main_keyboard_for_user(chat_id)
    enabled_callbacks = _callbacks(kb_enabled_again)
    _assert(
        any(cb == f"place_{place_id}" for cb in enabled_callbacks),
        f"sponsored row must reappear after re-enable: {enabled_callbacks}",
    )


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-sponsored-row-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: business sponsored-row runtime smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
