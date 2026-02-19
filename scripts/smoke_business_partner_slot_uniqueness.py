#!/usr/bin/env python3
"""
Smoke test: only one active Partner per category (service).

Checks:
- First owner can activate Partner.
- Second owner in the same category cannot activate Partner while first is active.
- Admin path is also blocked by the same rule.
- After first place is downgraded to Free, second owner can activate Partner.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
import types
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

ADMIN_ID = 42
OWNER_A = 5001
OWNER_B = 5002


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _setup_temp_db(db_path: Path) -> tuple[int, int, int]:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("Кав'ярні",))
        service_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        conn.execute(
            """
            INSERT INTO places(service_id, name, description, address, keywords, is_published, business_enabled)
            VALUES(?, 'Partner A', 'Desc', 'Addr A', 'a', 1, 1)
            """,
            (service_id,),
        )
        place_a = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        conn.execute(
            """
            INSERT INTO places(service_id, name, description, address, keywords, is_published, business_enabled)
            VALUES(?, 'Partner B', 'Desc', 'Addr B', 'b', 1, 1)
            """,
            (service_id,),
        )
        place_b = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        conn.execute(
            """
            INSERT INTO business_owners(place_id, tg_user_id, role, status, created_at, approved_at, approved_by)
            VALUES(?, ?, 'owner', 'approved', datetime('now'), datetime('now'), ?)
            """,
            (place_a, OWNER_A, ADMIN_ID),
        )
        conn.execute(
            """
            INSERT INTO business_owners(place_id, tg_user_id, role, status, created_at, approved_at, approved_by)
            VALUES(?, ?, 'owner', 'approved', datetime('now'), datetime('now'), ?)
            """,
            (place_b, OWNER_B, ADMIN_ID),
        )
        conn.commit()
        return service_id, place_a, place_b
    finally:
        conn.close()


async def _run_checks(service_id: int, place_a: int, place_b: int) -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    from business.repository import BusinessRepository  # noqa: WPS433
    from business.service import BusinessCabinetService, ValidationError  # noqa: WPS433

    repo = BusinessRepository()
    service = BusinessCabinetService(repository=repo)

    first = await service.change_subscription_tier(OWNER_A, int(place_a), "partner")
    _assert(str(first.get("tier") or "") == "partner", f"place A tier mismatch: {first}")
    _assert(str(first.get("status") or "") == "active", f"place A status mismatch: {first}")

    try:
        await service.change_subscription_tier(OWNER_B, int(place_b), "partner")
        raise AssertionError("Expected ValidationError for second Partner in same category")
    except ValidationError as error:
        msg = str(error)
        _assert("активний Partner" in msg, f"unexpected owner error message: {msg}")

    try:
        await service.admin_set_subscription_tier(
            ADMIN_ID,
            place_id=int(place_b),
            tier="partner",
            months=1,
        )
        raise AssertionError("Expected ValidationError for admin partner assignment conflict")
    except ValidationError as error:
        msg = str(error)
        _assert("активний Partner" in msg, f"unexpected admin error message: {msg}")

    await service.admin_set_subscription_tier(
        ADMIN_ID,
        place_id=int(place_a),
        tier="free",
        months=1,
    )

    second = await service.change_subscription_tier(OWNER_B, int(place_b), "partner")
    _assert(str(second.get("tier") or "") == "partner", f"place B tier mismatch: {second}")
    _assert(str(second.get("status") or "") == "active", f"place B status mismatch: {second}")

    partners = await repo.list_partner_subscriptions_by_service(int(service_id))
    _assert(len(partners) == 1, f"expected exactly one partner row, got: {partners}")
    _assert(int(partners[0].get("place_id") or 0) == int(place_b), f"wrong active partner row: {partners}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-biz-partner-slot-"))
    try:
        db_path = tmpdir / "state.db"
        service_id, place_a, place_b = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ["ADMIN_IDS"] = str(ADMIN_ID)
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks(service_id, place_a, place_b))
        print("OK: business partner slot uniqueness smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
