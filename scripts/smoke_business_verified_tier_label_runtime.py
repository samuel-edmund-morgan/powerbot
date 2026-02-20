#!/usr/bin/env python3
"""
Dynamic smoke test: resident verified tier display labels.

Validates runtime rendering for resident place-card:
- `verified_tier='pro'` must be shown as `Verified Premium` (not `Verified PRO`).
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
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_verified_tier_label__",))
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

    stamp = int(time.time())

    async with open_db() as db:
        async with db.execute(
            "SELECT id FROM general_services WHERE name = ? ORDER BY id DESC LIMIT 1",
            ("__smoke_verified_tier_label__",),
        ) as cur:
            row = await cur.fetchone()
        _assert(row is not None, "smoke service is missing")
        service_id = int(row[0])

        cur = await db.execute(
            """
            INSERT INTO places(
                service_id, name, description, address, keywords, is_published,
                business_enabled, is_verified, verified_tier
            ) VALUES(?, ?, ?, ?, ?, 1, 1, 1, 'pro')
            """,
            (
                service_id,
                f"Verified Label Smoke {stamp}",
                "Premium label smoke check",
                "SMOKE address without map",
                "premium verified",
            ),
        )
        place_id = int(cur.lastrowid)
        await db.commit()

    class _DummyMessage:
        def __init__(self) -> None:
            self.photo = None
            self.chat = SimpleNamespace(id=950001)
            self.message_id = 90
            self.edits: list[tuple[str, object]] = []
            self.answers: list[tuple[str, object]] = []

        async def delete(self):
            return True

        async def edit_text(self, text: str, reply_markup=None):
            self.edits.append((str(text), reply_markup))
            return SimpleNamespace(message_id=self.message_id)

        async def answer(self, text: str, reply_markup=None):
            self.answers.append((str(text), reply_markup))
            return SimpleNamespace(message_id=self.message_id + 1)

    msg = _DummyMessage()
    shown = await resident_handlers._render_place_detail_message(  # noqa: SLF001 - smoke targets runtime rendering
        msg,
        place_id=place_id,
        user_id=950002,
    )
    _assert(bool(shown), "place detail was not rendered")

    rendered_text = ""
    if msg.edits:
        rendered_text = msg.edits[-1][0]
    elif msg.answers:
        rendered_text = msg.answers[-1][0]
    _assert(rendered_text, "no rendered text captured")

    _assert(
        "✅ <b>Verified Premium</b>" in rendered_text,
        f"expected 'Verified Premium' label missing in resident card:\n{rendered_text}",
    )
    _assert(
        "✅ <b>Verified PRO</b>" not in rendered_text,
        f"legacy technical label 'Verified PRO' must not appear:\n{rendered_text}",
    )


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-verified-tier-label-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: business verified tier label runtime smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()

