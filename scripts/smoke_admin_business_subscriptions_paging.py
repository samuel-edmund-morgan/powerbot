#!/usr/bin/env python3
"""
Smoke test for admin business subscriptions paging/export contract.

What it validates:
- `list_all_subscriptions_admin()` returns stable paged slices with correct total.
- pages do not overlap and cover all rows when iterated.
- owner fields are present in rows (for admin contact visibility).
- export-like loop (page_size=50) collects the full dataset.

Run:
  python3 scripts/smoke_admin_business_subscriptions_paging.py
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

TOTAL_ROWS = 123


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _setup_temp_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        schema = SCHEMA_SQL.read_text(encoding="utf-8")
        conn.executescript(schema)
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("Кав'ярні",))

        created_at = _now_iso()
        for idx in range(1, TOTAL_ROWS + 1):
            conn.execute(
                """
                INSERT INTO places(
                    service_id, name, description, address, keywords,
                    is_published,
                    is_verified, verified_tier, verified_until, business_enabled
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    f"Place {idx:03d}",
                    "Desc",
                    f"Addr {idx}",
                    f"place {idx}",
                    1 if idx % 3 else 0,
                    1 if idx % 4 == 0 else 0,
                    "light" if idx % 4 == 0 else None,
                    (datetime.now(timezone.utc) + timedelta(days=30)).isoformat() if idx % 4 == 0 else None,
                    1,
                ),
            )
            place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            tg_user_id = 20000 + idx

            conn.execute(
                """
                INSERT INTO business_owners(
                    place_id, tg_user_id, role, status, created_at, approved_at, approved_by
                ) VALUES(?, ?, 'owner', ?, ?, ?, ?)
                """,
                (
                    place_id,
                    tg_user_id,
                    "approved" if idx % 5 else "pending",
                    created_at,
                    created_at,
                    1,
                ),
            )
            tier = "free"
            status = "inactive"
            expires_at = None
            if idx % 2 == 0:
                tier = "light"
                status = "active"
                expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
            if idx % 7 == 0:
                tier = "pro"
                status = "past_due"
                expires_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            conn.execute(
                """
                INSERT INTO business_subscriptions(
                    place_id, tier, status, starts_at, expires_at, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    place_id,
                    tier,
                    status,
                    created_at if status != "inactive" else None,
                    expires_at,
                    created_at,
                    created_at,
                ),
            )
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

    from business.service import BusinessCabinetService  # noqa: WPS433

    service = BusinessCabinetService()
    admin_id = 1

    page0_rows, total = await service.list_all_subscriptions_admin(admin_id, limit=10, offset=0)
    page1_rows, total_again = await service.list_all_subscriptions_admin(admin_id, limit=10, offset=10)

    _assert(int(total) == TOTAL_ROWS, f"unexpected total: {total}")
    _assert(int(total_again) == TOTAL_ROWS, f"unexpected total on second page: {total_again}")
    _assert(len(page0_rows) == 10, f"unexpected page0 size: {len(page0_rows)}")
    _assert(len(page1_rows) == 10, f"unexpected page1 size: {len(page1_rows)}")

    page0_ids = {int(row.get("place_id") or 0) for row in page0_rows}
    page1_ids = {int(row.get("place_id") or 0) for row in page1_rows}
    _assert(page0_ids.isdisjoint(page1_ids), "paging overlap detected between first pages")

    for row in page0_rows + page1_rows:
        _assert("owner_tg_user_id" in row, f"owner_tg_user_id missing in row: {row}")
        _assert("owner_status" in row, f"owner_status missing in row: {row}")

    # Emulate export pagination loop from admin handler.
    all_rows: list[dict] = []
    offset = 0
    page_size = 50
    while True:
        rows, loop_total = await service.list_all_subscriptions_admin(admin_id, limit=page_size, offset=offset)
        all_rows.extend(rows)
        offset += len(rows)
        if not rows or offset >= int(loop_total):
            break

    _assert(len(all_rows) == TOTAL_ROWS, f"export loop count mismatch: {len(all_rows)} != {TOTAL_ROWS}")
    all_ids = [int(row.get("place_id") or 0) for row in all_rows]
    _assert(len(set(all_ids)) == TOTAL_ROWS, "duplicate/missing place ids in export loop")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-admin-subs-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ["ADMIN_IDS"] = "1"
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: admin business subscriptions paging smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
