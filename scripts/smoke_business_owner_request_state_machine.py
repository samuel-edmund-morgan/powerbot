#!/usr/bin/env python3
"""
Smoke test: owner-request moderation state machine.

Validates:
- non-admin access to moderation API is denied
- pending request can be approved exactly once
- pending request can be rejected exactly once
- terminal states (approved/rejected) cannot be reprocessed

Run:
  python3 scripts/smoke_business_owner_request_state_machine.py
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
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("Кав'ярні",))
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

    from business.repository import BusinessRepository  # noqa: WPS433
    from business.service import (  # noqa: WPS433
        AccessDeniedError,
        BusinessCabinetService,
        ValidationError,
    )

    repo = BusinessRepository()
    service = BusinessCabinetService(repository=repo)

    admin_id = 1
    non_admin_id = 999
    service.admin_ids.add(admin_id)

    # Non-admin guard.
    try:
        await service.list_pending_owner_requests(non_admin_id)
    except AccessDeniedError:
        pass
    else:
        raise AssertionError("non-admin must not access moderation queue")

    stamp = int(time.time())

    created_a = await service.register_new_business(
        tg_user_id=71000 + stamp % 10000,
        service_id=1,
        place_name=f"State Machine Approve {stamp}",
        description="desc",
        address="addr",
    )
    owner_a = created_a.get("owner") or {}
    owner_a_id = int(owner_a.get("id") or 0)
    _assert(owner_a_id > 0, f"invalid owner request A: {created_a}")

    approved = await service.approve_owner_request(admin_id, owner_a_id)
    _assert(str(approved.get("status") or "") == "approved", f"approve failed: {approved}")

    for op_name, op in (
        ("approve_after_approved", service.approve_owner_request),
        ("reject_after_approved", service.reject_owner_request),
    ):
        try:
            await op(admin_id, owner_a_id)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"{op_name} must fail with ValidationError")

    created_b = await service.register_new_business(
        tg_user_id=72000 + stamp % 10000,
        service_id=1,
        place_name=f"State Machine Reject {stamp}",
        description="desc",
        address="addr",
    )
    owner_b = created_b.get("owner") or {}
    owner_b_id = int(owner_b.get("id") or 0)
    _assert(owner_b_id > 0, f"invalid owner request B: {created_b}")

    rejected = await service.reject_owner_request(admin_id, owner_b_id)
    _assert(str(rejected.get("status") or "") == "rejected", f"reject failed: {rejected}")

    for op_name, op in (
        ("reject_after_rejected", service.reject_owner_request),
        ("approve_after_rejected", service.approve_owner_request),
    ):
        try:
            await op(admin_id, owner_b_id)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"{op_name} must fail with ValidationError")

    owner_a_row = await repo.get_owner_request(owner_a_id)
    owner_b_row = await repo.get_owner_request(owner_b_id)
    _assert(str(owner_a_row.get("status") or "") == "approved", f"owner A final status mismatch: {owner_a_row}")
    _assert(str(owner_b_row.get("status") or "") == "rejected", f"owner B final status mismatch: {owner_b_row}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-owner-request-sm-"))
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
        print("OK: business owner-request state-machine smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
