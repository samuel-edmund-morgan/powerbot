#!/usr/bin/env python3
"""
Dynamic smoke test: sponsored row in resident main menu.

Validates:
- Partner place can be shown as sponsored row in main menu (up to daily limit per user).
- User can disable/enable sponsored offers via notifications setting.
- Notifications keyboard reflects sponsored toggle status.
- Partner rotation selection follows `sponsored_rotation_hours` window.
- Invalid/empty `sponsored_rotation_hours` falls back to 48h window.
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

    from database import db_set, get_partner_places_for_sponsored, open_db  # noqa: WPS433
    import handlers as resident_handlers  # noqa: WPS433

    stamp = int(time.time())
    chat_id = 960000 + (stamp % 10000)
    created_partner_ids: set[int] = set()

    async with open_db() as db:
        async with db.execute(
            "SELECT id FROM general_services WHERE name = ? ORDER BY id DESC LIMIT 1",
            ("__smoke_sponsored_row__",),
        ) as cur:
            row = await cur.fetchone()
        _assert(row is not None, "smoke service missing")
        service_id = int(row[0])

        # Create several Partner places to validate both row-visibility and rotation logic.
        partner_specs = [
            (f"Partner Sponsor A {stamp}", 7),
            (f"Partner Sponsor B {stamp}", 5),
            (f"Partner Sponsor C {stamp}", 1),
        ]
        for idx, (name, likes) in enumerate(partner_specs, start=1):
            cur = await db.execute(
                """
                INSERT INTO places(
                    service_id, name, description, address, keywords, is_published,
                    business_enabled, is_verified, verified_tier
                ) VALUES(?, ?, ?, ?, ?, 1, 1, 1, 'partner')
                """,
                (
                    service_id,
                    name,
                    "smoke sponsor place",
                    f"SMOKE sponsored address {idx}",
                    f"sponsor smoke {idx}",
                ),
            )
            place_id = int(cur.lastrowid)
            created_partner_ids.add(place_id)
            for vote in range(likes):
                await db.execute(
                    "INSERT INTO place_likes(place_id, chat_id, liked_at) VALUES(?, ?, datetime('now'))",
                    (place_id, 9800000 + place_id * 100 + vote),
                )
        await db.commit()

    # 1) First main-menu render: sponsored row should appear.
    kb_first = await resident_handlers.get_main_keyboard_for_user(chat_id)
    first_callbacks = _callbacks(kb_first)
    sponsored_first = next((cb for cb in first_callbacks if cb.startswith("place_")), "")
    _assert(bool(sponsored_first), f"sponsored row callback missing on first render: {first_callbacks}")
    sponsored_first_id = int(sponsored_first.split("_", 1)[1])
    _assert(
        sponsored_first_id in created_partner_ids,
        f"unexpected sponsored place on first render: {sponsored_first_id}",
    )

    # 2) Same-day render budget: sponsored row appears up to limit, then hides.
    for idx in range(2, 6):
        kb = await resident_handlers.get_main_keyboard_for_user(chat_id)
        callbacks = _callbacks(kb)
        _assert(
            any(cb.startswith("place_") and int(cb.split("_", 1)[1]) in created_partner_ids for cb in callbacks),
            f"sponsored row must be visible on same-day render #{idx}: {callbacks}",
        )
    kb_sixth = await resident_handlers.get_main_keyboard_for_user(chat_id)
    callbacks_sixth = _callbacks(kb_sixth)
    _assert(
        not any(cb.startswith("place_") and int(cb.split("_", 1)[1]) in created_partner_ids for cb in callbacks_sixth),
        f"sponsored row must be throttled after daily limit: {callbacks_sixth}",
    )

    # 3) Notifications menu should contain sponsored toggle and be ON by default.
    notif_kb_on = await resident_handlers.get_notifications_keyboard(chat_id)
    toggle_text_on = _button_text_by_callback(notif_kb_on, "notif_toggle_sponsored")
    _assert(bool(toggle_text_on), "notifications keyboard missing `notif_toggle_sponsored` button")
    _assert("✅" in toggle_text_on, f"sponsored toggle must be ON by default, got: {toggle_text_on}")

    # 4) Disable sponsored offers -> menu should hide row.
    await resident_handlers._set_sponsored_offers_enabled(chat_id, False)  # noqa: SLF001 - runtime smoke of internal contract
    await db_set(resident_handlers._sponsored_seen_counter_key(chat_id), "")  # noqa: SLF001 - reset throttle for deterministic check
    await db_set(resident_handlers._sponsored_last_seen_day_key(chat_id), "")  # noqa: SLF001 - legacy marker reset
    kb_disabled = await resident_handlers.get_main_keyboard_for_user(chat_id)
    disabled_callbacks = _callbacks(kb_disabled)
    _assert(
        not any(
            cb.startswith("place_") and int(cb.split("_", 1)[1]) in created_partner_ids
            for cb in disabled_callbacks
        ),
        f"sponsored row must be hidden when disabled: {disabled_callbacks}",
    )
    notif_kb_off = await resident_handlers.get_notifications_keyboard(chat_id)
    toggle_text_off = _button_text_by_callback(notif_kb_off, "notif_toggle_sponsored")
    _assert("❌" in toggle_text_off, f"sponsored toggle must show OFF status, got: {toggle_text_off}")

    # 5) Enable back and rewind throttle day -> row should appear again.
    await resident_handlers._set_sponsored_offers_enabled(chat_id, True)  # noqa: SLF001
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    await db_set(resident_handlers._sponsored_seen_counter_key(chat_id), f"{yesterday}|5")  # noqa: SLF001
    await db_set(resident_handlers._sponsored_last_seen_day_key(chat_id), yesterday)  # noqa: SLF001
    kb_enabled_again = await resident_handlers.get_main_keyboard_for_user(chat_id)
    enabled_callbacks = _callbacks(kb_enabled_again)
    _assert(
        any(
            cb.startswith("place_") and int(cb.split("_", 1)[1]) in created_partner_ids
            for cb in enabled_callbacks
        ),
        f"sponsored row must reappear after re-enable: {enabled_callbacks}",
    )

    # 6) Rotation contract: selection follows window size, invalid config falls back to 48h.
    places_ordered = await get_partner_places_for_sponsored()
    ordered_ids = [int(item.get("id") or 0) for item in places_ordered if int(item.get("id") or 0) in created_partner_ids]
    _assert(len(ordered_ids) >= 3, f"expected >=3 partner places for rotation, got: {ordered_ids}")

    original_datetime = resident_handlers.datetime

    class _FakeDateTime:
        current = datetime(2026, 2, 1, 0, 5, 0)

        @classmethod
        def utcnow(cls):
            return cls.current

    try:
        resident_handlers.datetime = _FakeDateTime  # type: ignore[assignment]

        # Explicit 1h rotation window.
        await db_set("sponsored_rotation_hours", "1")

        t0 = datetime(2026, 2, 1, 0, 5, 0)
        _FakeDateTime.current = t0
        picked_t0 = await resident_handlers._pick_sponsored_partner_place()  # noqa: SLF001
        slot_t0 = int(t0.timestamp() // 3600) % len(ordered_ids)
        expected_t0 = ordered_ids[slot_t0]
        _assert(int((picked_t0 or {}).get("id") or 0) == expected_t0, f"rotation(1h) t0 mismatch: got={picked_t0} expected={expected_t0}")

        # Same window -> same place.
        t_same = datetime(2026, 2, 1, 0, 35, 0)
        _FakeDateTime.current = t_same
        picked_same = await resident_handlers._pick_sponsored_partner_place()  # noqa: SLF001
        _assert(
            int((picked_same or {}).get("id") or 0) == expected_t0,
            f"rotation(1h) same-window mismatch: got={picked_same} expected={expected_t0}",
        )

        # Next window -> deterministic next slot.
        t_next = datetime(2026, 2, 1, 1, 6, 0)
        _FakeDateTime.current = t_next
        picked_next = await resident_handlers._pick_sponsored_partner_place()  # noqa: SLF001
        slot_next = int(t_next.timestamp() // 3600) % len(ordered_ids)
        expected_next = ordered_ids[slot_next]
        _assert(
            int((picked_next or {}).get("id") or 0) == expected_next,
            f"rotation(1h) next-window mismatch: got={picked_next} expected={expected_next}",
        )

        # Invalid value -> fallback 48h.
        await db_set("sponsored_rotation_hours", "invalid")
        base_ts = (48 * 3600) * 500 + 1000  # stable point inside a 48h window
        t_fallback_a = datetime.utcfromtimestamp(base_ts)
        t_fallback_b = datetime.utcfromtimestamp(base_ts + 3600)  # still same 48h window
        t_fallback_c = datetime.utcfromtimestamp(base_ts + (48 * 3600))  # next 48h window

        _FakeDateTime.current = t_fallback_a
        picked_a = await resident_handlers._pick_sponsored_partner_place()  # noqa: SLF001
        slot_a = int(t_fallback_a.timestamp() // (48 * 3600)) % len(ordered_ids)
        expected_a = ordered_ids[slot_a]
        _assert(int((picked_a or {}).get("id") or 0) == expected_a, f"rotation(48h fallback) A mismatch: got={picked_a} expected={expected_a}")

        _FakeDateTime.current = t_fallback_b
        picked_b = await resident_handlers._pick_sponsored_partner_place()  # noqa: SLF001
        _assert(
            int((picked_b or {}).get("id") or 0) == expected_a,
            f"rotation(48h fallback) same-window mismatch: got={picked_b} expected={expected_a}",
        )

        _FakeDateTime.current = t_fallback_c
        picked_c = await resident_handlers._pick_sponsored_partner_place()  # noqa: SLF001
        slot_c = int(t_fallback_c.timestamp() // (48 * 3600)) % len(ordered_ids)
        expected_c = ordered_ids[slot_c]
        _assert(int((picked_c or {}).get("id") or 0) == expected_c, f"rotation(48h fallback) C mismatch: got={picked_c} expected={expected_c}")
    finally:
        resident_handlers.datetime = original_datetime  # type: ignore[assignment]


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
