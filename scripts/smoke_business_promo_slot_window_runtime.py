#!/usr/bin/env python3
"""
Dynamic smoke test: Premium(Pro) promo-slot window runtime contract.

Validates resident catalog behavior in BUSINESS_MODE:
- only Pro places with active `promo_slot_until` can occupy 🔝 promo slot;
- expired Pro promo-slot does not get 🔝 marker even with higher likes;
- expired Pro place stays in verified-by-likes block with ✅ marker.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
import types
from datetime import datetime, timedelta, timezone
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
        conn.commit()
    finally:
        conn.close()


def _button_style(button) -> str | None:
    for attr in ("style", "_style"):
        value = getattr(button, attr, None)
        if value:
            return str(value)
    return None


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

    service_name = "__smoke_promo_slot_window_runtime__"
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    past_iso = (now - timedelta(days=1)).isoformat()
    future_iso = (now + timedelta(days=7)).isoformat()

    async with open_db() as db:
        await db.execute("INSERT INTO general_services(name) VALUES(?)", (service_name,))
        await db.commit()

        async with db.execute(
            "SELECT id FROM general_services WHERE name = ? ORDER BY id DESC LIMIT 1",
            (service_name,),
        ) as cur:
            row = await cur.fetchone()
        _assert(row is not None, "failed to create smoke service")
        service_id = int(row[0])

        async def _insert_place(
            *,
            name: str,
            likes: int,
            verified: bool,
            tier: str | None,
            promo_slot_until: str | None,
            business_enabled: bool,
        ) -> int:
            cur = await db.execute(
                """
                INSERT INTO places(
                    service_id, name, description, address, keywords, is_published,
                    business_enabled, is_verified, verified_tier, promo_slot_until
                ) VALUES(?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    service_id,
                    name,
                    "smoke",
                    "smoke addr",
                    name.lower(),
                    1 if business_enabled else 0,
                    1 if verified else 0,
                    tier,
                    promo_slot_until,
                ),
            )
            place_id = int(cur.lastrowid)
            for idx in range(int(likes)):
                chat_id = int(place_id * 10_000 + idx + 1)
                await db.execute(
                    """
                    INSERT INTO place_likes(place_id, chat_id, liked_at)
                    VALUES(?, ?, ?)
                    """,
                    (place_id, chat_id, now_iso),
                )
            return place_id

        partner_id = await _insert_place(
            name="Partner Slot",
            likes=4,
            verified=True,
            tier="partner",
            promo_slot_until=None,
            business_enabled=True,
        )
        pro_active_id = await _insert_place(
            name="Pro Active Promo",
            likes=2,
            verified=True,
            tier="pro",
            promo_slot_until=future_iso,
            business_enabled=True,
        )
        pro_expired_id = await _insert_place(
            name="Pro Expired Promo",
            likes=50,
            verified=True,
            tier="pro",
            promo_slot_until=past_iso,
            business_enabled=True,
        )
        light_id = await _insert_place(
            name="Light Verified",
            likes=3,
            verified=True,
            tier="light",
            promo_slot_until=None,
            business_enabled=True,
        )
        unverified_id = await _insert_place(
            name="Unverified High",
            likes=40,
            verified=False,
            tier=None,
            promo_slot_until=None,
            business_enabled=False,
        )
        await db.commit()

    class _DummyMessage:
        def __init__(self) -> None:
            self.photo = None
            self.chat = SimpleNamespace(id=920003)
            self.message_id = 77
            self.edits: list[tuple[str, object]] = []

        async def edit_text(self, text: str, reply_markup=None):
            self.edits.append((str(text), reply_markup))
            return SimpleNamespace(message_id=self.message_id)

        async def answer(self, text: str, reply_markup=None):
            self.edits.append((str(text), reply_markup))
            return SimpleNamespace(message_id=self.message_id + 1)

    class _DummyCallback:
        def __init__(self, service_id_value: int):
            self.data = f"places_cat_{service_id_value}"
            self.from_user = SimpleNamespace(id=930003, username="smoke_user", first_name="Smoke")
            self.message = _DummyMessage()
            self.answered: list[tuple[tuple, dict]] = []

        async def answer(self, *args, **kwargs):
            self.answered.append((args, kwargs))
            return True

    callback = _DummyCallback(service_id)
    await resident_handlers.cb_places_category(callback)

    _assert(callback.message.edits, "catalog handler did not render response")
    text, reply_markup = callback.message.edits[-1]
    _assert("⭐ офіційний партнер • 🔝 промо • ✅ verified" in text, f"ranking hint missing: {text}")

    place_buttons = [
        row[0]
        for row in getattr(reply_markup, "inline_keyboard", [])
        if row and str(getattr(row[0], "callback_data", "")).startswith("place_")
    ]
    _assert(len(place_buttons) == 5, f"expected 5 place buttons, got {len(place_buttons)}")

    actual_order = [int(str(btn.callback_data).split("_", 1)[1]) for btn in place_buttons]
    expected_order = [partner_id, pro_active_id, pro_expired_id, light_id, unverified_id]
    _assert(actual_order == expected_order, f"catalog order mismatch:\nactual={actual_order}\nexpected={expected_order}")

    labels = {
        int(str(btn.callback_data).split("_", 1)[1]): str(getattr(btn, "text", "") or "")
        for btn in place_buttons
    }

    _assert(labels[partner_id].startswith("🥇 ⭐ "), f"partner label mismatch: {labels[partner_id]}")
    _assert(labels[pro_active_id].startswith("🥈 🔝 "), f"active pro promo label mismatch: {labels[pro_active_id]}")
    _assert(labels[pro_expired_id].startswith("🥉 ✅ "), f"expired pro label mismatch: {labels[pro_expired_id]}")
    _assert("🔝" not in labels[pro_expired_id], f"expired pro must not keep promo marker: {labels[pro_expired_id]}")

    promo_labels = [label for label in labels.values() if "🔝 " in label]
    _assert(len(promo_labels) == 1, f"expected exactly one promo slot label, got: {promo_labels}")

    styles = [_button_style(place_buttons[0]), _button_style(place_buttons[1])]
    if any(styles):
        _assert(styles[0] == "success", f"partner slot style mismatch: {styles[0]}")
        _assert(styles[1] == "primary", f"pro slot style mismatch: {styles[1]}")



def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-promo-slot-window-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ["BUSINESS_MODE"] = "1"
        os.environ.setdefault("ADMIN_IDS", "1")

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: business promo-slot window runtime smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
