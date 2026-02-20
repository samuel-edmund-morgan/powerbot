#!/usr/bin/env python3
"""
Dynamic smoke test: init_db() legacy places-table backfill.

Goal:
- Protect against regressions when DB was created long ago with minimal
  `places` schema (without business/publish/profile columns).
- Ensure `init_db()` adds all required columns/indexes for resident/business
  flows and keeps existing rows readable.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
import types
from pathlib import Path


REQUIRED_PLACE_COLUMNS = {
    "id",
    "service_id",
    "name",
    "description",
    "address",
    "keywords",
    "is_published",
    "is_verified",
    "verified_tier",
    "verified_until",
    "business_enabled",
    "opening_hours",
    "contact_type",
    "contact_value",
    "link_url",
    "logo_url",
    "photo_1_url",
    "photo_2_url",
    "photo_3_url",
    "promo_code",
    "menu_url",
    "order_url",
    "offer_1_text",
    "offer_2_text",
    "offer_1_image_url",
    "offer_2_image_url",
}


def _resolve_repo_root() -> Path:
    candidates: list[Path] = []
    try:
        candidates.append(Path(__file__).resolve().parents[1])
    except Exception:
        pass
    candidates.extend([Path.cwd(), Path("/app"), Path("/workspace")])
    for root in candidates:
        if (root / "src").exists() and (root / "schema.sql").exists():
            return root
    raise FileNotFoundError("Cannot locate repo root with src/ and schema.sql")


REPO_ROOT = _resolve_repo_root()


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _seed_legacy_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        # Simulate very old schema where places lacked business/publish columns.
        conn.execute(
            """
            CREATE TABLE general_services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE places (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                address TEXT,
                FOREIGN KEY (service_id) REFERENCES general_services(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("Кав'ярні",))
        conn.execute(
            """
            INSERT INTO places(service_id, name, description, address)
            VALUES(1, 'Legacy Place', 'Legacy Desc', 'Legacy Addr')
            """
        )
        conn.commit()
    finally:
        conn.close()


def _fetch_place_columns(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("PRAGMA table_info(places)").fetchall()
        return {str(row[1]) for row in rows}
    finally:
        conn.close()


def _fetch_place_indexes(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("PRAGMA index_list(places)").fetchall()
        return {str(row[1]) for row in rows}
    finally:
        conn.close()


def _table_exists(db_path: Path, table_name: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (str(table_name),),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _fetch_legacy_row(db_path: Path) -> tuple[str, str, str] | None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT name, description, address FROM places WHERE id=1",
        ).fetchone()
        if not row:
            return None
        return str(row[0]), str(row[1]), str(row[2])
    finally:
        conn.close()


def _fetch_is_published(db_path: Path) -> int | None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT is_published FROM places WHERE id=1").fetchone()
        return int(row[0]) if row is not None and row[0] is not None else None
    finally:
        conn.close()


async def _run_checks(db_path: Path) -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    from database import init_db  # noqa: WPS433

    await init_db()

    cols = _fetch_place_columns(db_path)
    missing_cols = sorted(REQUIRED_PLACE_COLUMNS - cols)
    _assert(not missing_cols, f"missing legacy backfill columns in places: {missing_cols}")

    idx = _fetch_place_indexes(db_path)
    _assert("idx_places_service_published" in idx, f"missing idx_places_service_published; got={sorted(idx)}")

    # New runtime tables required by current features.
    _assert(_table_exists(db_path, "place_clicks_daily"), "table place_clicks_daily was not created by init_db")
    _assert(_table_exists(db_path, "place_reports"), "table place_reports was not created by init_db")
    _assert(_table_exists(db_path, "sensor_public_ids"), "table sensor_public_ids was not created by init_db")

    # Legacy row must survive migration.
    row = _fetch_legacy_row(db_path)
    _assert(row is not None, "legacy places row was lost")
    _assert(row[0] == "Legacy Place", f"legacy row name mismatch: {row}")
    _assert(row[1] == "Legacy Desc", f"legacy row description mismatch: {row}")
    _assert(row[2] == "Legacy Addr", f"legacy row address mismatch: {row}")

    is_published = _fetch_is_published(db_path)
    _assert(is_published in {0, 1}, f"legacy row has invalid is_published: {is_published}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-legacy-places-"))
    try:
        db_path = tmpdir / "state.db"
        _seed_legacy_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "1")

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))
        asyncio.run(_run_checks(db_path))
        print("OK: init_db legacy places backfill smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
