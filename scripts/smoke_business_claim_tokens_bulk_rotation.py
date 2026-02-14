#!/usr/bin/env python3
"""
Smoke test: bulk claim-token rotation for all places.

Validates:
- bulk rotation creates one active token per place
- second bulk rotation revokes previous active tokens and creates new ones
- tokens remain unique across places
- audit log records bulk rotation action for each place

Run:
  python3 scripts/smoke_business_claim_tokens_bulk_rotation.py
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _setup_temp_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_bulk_claim_tokens__",))
        service_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        created_at = _now_iso()
        for idx in range(1, 4):
            conn.execute(
                """
                INSERT INTO places(
                    service_id, name, description, address, keywords,
                    is_published, is_verified, verified_tier, verified_until, business_enabled
                ) VALUES(?, ?, 'Desc', 'Addr', ?, 1, 0, NULL, NULL, 0)
                """,
                (service_id, f"Bulk Token Place {idx}", f"bulk{idx}"),
            )
            place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
                """
                INSERT INTO business_owners(
                    place_id, tg_user_id, role, status, created_at, approved_at, approved_by
                ) VALUES(?, ?, 'owner', 'approved', ?, ?, ?)
                """,
                (place_id, 50000 + idx, created_at, created_at, 1),
            )
        conn.commit()
    finally:
        conn.close()


async def _fetch_active_tokens(db_path: str) -> dict[int, str]:
    import aiosqlite  # noqa: WPS433

    rows: list[tuple[int, str]] = []
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            """
            SELECT place_id, token
              FROM business_claim_tokens
             WHERE status = 'active'
             ORDER BY place_id
            """
        ) as cur:
            rows = await cur.fetchall()
    return {int(place_id): str(token) for place_id, token in rows}


async def _run_checks() -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    from business.repository import BusinessRepository  # noqa: WPS433
    from business.service import BusinessCabinetService  # noqa: WPS433

    repo = BusinessRepository()
    service = BusinessCabinetService(repository=repo)

    admin_id = 1
    service.admin_ids.add(admin_id)

    first = await service.bulk_rotate_claim_tokens_for_all_places(admin_id)
    _assert(int(first.get("total_places") or 0) == 3, f"unexpected total places (first): {first}")
    _assert(int(first.get("rotated") or 0) == 3, f"unexpected rotated count (first): {first}")

    first_tokens = await _fetch_active_tokens(os.environ["DB_PATH"])
    _assert(len(first_tokens) == 3, f"must have 3 active tokens after first bulk: {first_tokens}")
    _assert(len(set(first_tokens.values())) == 3, f"active tokens must be unique: {first_tokens}")

    second = await service.bulk_rotate_claim_tokens_for_all_places(admin_id)
    _assert(int(second.get("total_places") or 0) == 3, f"unexpected total places (second): {second}")
    _assert(int(second.get("rotated") or 0) == 3, f"unexpected rotated count (second): {second}")

    second_tokens = await _fetch_active_tokens(os.environ["DB_PATH"])
    _assert(len(second_tokens) == 3, f"must have 3 active tokens after second bulk: {second_tokens}")
    _assert(len(set(second_tokens.values())) == 3, f"active tokens must be unique after second run: {second_tokens}")
    _assert(first_tokens.keys() == second_tokens.keys(), "place ids mismatch between runs")
    for place_id, token in first_tokens.items():
        _assert(second_tokens[place_id] != token, f"token for place {place_id} was not rotated")

    import aiosqlite  # noqa: WPS433

    async with aiosqlite.connect(os.environ["DB_PATH"]) as db:
        async with db.execute(
            """
            SELECT COUNT(*)
              FROM business_claim_tokens
             WHERE status = 'revoked'
            """
        ) as cur:
            revoked_count = int((await cur.fetchone())[0])

        async with db.execute(
            """
            SELECT COUNT(*)
              FROM business_audit_log
             WHERE action = 'claim_token_rotated_admin_ui_bulk'
            """
        ) as cur:
            audit_count = int((await cur.fetchone())[0])

    _assert(revoked_count >= 3, f"old active tokens should be revoked after second run: revoked={revoked_count}")
    _assert(audit_count == 6, f"bulk rotation audit rows mismatch (expected 6): {audit_count}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-claim-bulk-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "1")
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: business claim-token bulk rotation smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
