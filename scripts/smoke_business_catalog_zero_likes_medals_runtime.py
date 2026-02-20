#!/usr/bin/env python3
"""
Dynamic smoke test: no medals for zero-like catalog rows in BUSINESS_MODE.

Validates resident `cb_places_category` runtime behavior:
- ranking order still follows partner -> promo(pro) -> verified -> unverified;
- when all places have 0 likes, no 🥇/🥈/🥉 medal is rendered.
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

    service_name = "__smoke_catalog_zero_like_medals__"

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
            verified: bool,
            tier: str | None,
            business_enabled: bool,
        ) -> int:
            cur = await db.execute(
                """
                INSERT INTO places(
                    service_id, name, description, address, keywords, is_published,
                    business_enabled, is_verified, verified_tier
                ) VALUES(?, ?, ?, ?, ?, 1, ?, ?, ?)
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
                ),
            )
            return int(cur.lastrowid)

        partner_id = await _insert_place(
            name="Partner Zero",
            verified=True,
            tier="partner",
            business_enabled=True,
        )
        pro_id = await _insert_place(
            name="Pro Zero",
            verified=True,
            tier="pro",
            business_enabled=True,
        )
        light_id = await _insert_place(
            name="Light Zero",
            verified=True,
            tier="light",
            business_enabled=True,
        )
        unverified_id = await _insert_place(
            name="Unverified Zero",
            verified=False,
            tier=None,
            business_enabled=False,
        )
        await db.commit()

    class _DummyMessage:
        def __init__(self) -> None:
            self.photo = None
            self.chat = SimpleNamespace(id=940001)
            self.message_id = 101
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
            self.from_user = SimpleNamespace(id=940002, username="zero_likes", first_name="Zero")
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
    _assert(len(place_buttons) == 4, f"expected 4 place buttons, got {len(place_buttons)}")

    actual_order = [int(str(btn.callback_data).split("_", 1)[1]) for btn in place_buttons]
    expected_order = [partner_id, pro_id, light_id, unverified_id]
    _assert(actual_order == expected_order, f"order mismatch: actual={actual_order}, expected={expected_order}")

    labels = [str(getattr(btn, "text", "") or "") for btn in place_buttons]
    for label in labels:
        _assert(not label.startswith("🥇 "), f"unexpected medal in zero-like list: {label}")
        _assert(not label.startswith("🥈 "), f"unexpected medal in zero-like list: {label}")
        _assert(not label.startswith("🥉 "), f"unexpected medal in zero-like list: {label}")

    label_by_id = {
        int(str(btn.callback_data).split("_", 1)[1]): str(getattr(btn, "text", "") or "")
        for btn in place_buttons
    }
    _assert(label_by_id[partner_id].startswith("⭐ "), f"partner marker missing: {label_by_id[partner_id]}")
    _assert(label_by_id[pro_id].startswith("🔝 "), f"pro marker missing: {label_by_id[pro_id]}")
    _assert(label_by_id[light_id].startswith("✅ "), f"verified marker missing: {label_by_id[light_id]}")
    _assert(
        not label_by_id[unverified_id].startswith(("⭐ ", "🔝 ", "✅ ")),
        f"unverified row must not have verified marker: {label_by_id[unverified_id]}",
    )


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-catalog-zero-medals-"))
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
        print("OK: business catalog zero-like medals runtime smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
