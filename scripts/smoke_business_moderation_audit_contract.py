#!/usr/bin/env python3
"""
Smoke test: moderation approve/reject audit contract.

Validates for owner-request moderation flow:
- approve: owner status -> approved, place published/business_enabled.
- reject: owner status -> rejected, place remains unpublished/business disabled.
- audit logs contain owner_request_created + terminal moderation action.
- terminal audit payload includes expected owner identifiers.
"""

from __future__ import annotations

import asyncio
import json
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
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_moderation_audit__",))
        conn.commit()
    finally:
        conn.close()


def _safe_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


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

    admin_id = 700001
    service.admin_ids.add(admin_id)

    services = await repo.list_services()
    _assert(bool(services), "no services available in temp DB")
    service_id = int(services[0].get("id") or 0)
    _assert(service_id > 0, f"invalid service id: {services[0]}")

    stamp = int(time.time())

    # Approve branch.
    created_a = await service.register_new_business(
        tg_user_id=710000 + stamp % 10000,
        service_id=service_id,
        place_name=f"Moderation Audit Approve {stamp}",
        description="approve branch",
        address="addr-a",
    )
    owner_a = created_a.get("owner") or {}
    place_a = created_a.get("place") or {}
    owner_a_id = int(owner_a.get("id") or 0)
    place_a_id = int(place_a.get("id") or 0)
    _assert(owner_a_id > 0 and place_a_id > 0, f"invalid approve branch objects: {created_a}")

    approved = await service.approve_owner_request(admin_id, owner_a_id)
    _assert(str(approved.get("status") or "") == "approved", f"approve status mismatch: {approved}")

    owner_a_row = await repo.get_owner_request(owner_a_id)
    place_a_row = await repo.get_place(place_a_id)
    _assert(owner_a_row is not None, "approve: owner row missing")
    _assert(place_a_row is not None, "approve: place row missing")
    _assert(str(owner_a_row.get("status") or "") == "approved", f"approve: owner status mismatch: {owner_a_row}")
    _assert(int(place_a_row.get("is_published") or 0) == 1, f"approve: place must be published: {place_a_row}")
    _assert(int(place_a_row.get("business_enabled") or 0) == 1, f"approve: business_enabled must be 1: {place_a_row}")

    audit_a = await repo.list_business_audit_logs(limit=100, offset=0, place_id=place_a_id)
    actions_a = [str(row.get("action") or "") for row in audit_a]
    _assert("owner_request_created" in actions_a, f"approve: missing owner_request_created audit: {actions_a}")
    _assert("owner_request_approved" in actions_a, f"approve: missing owner_request_approved audit: {actions_a}")
    approved_log = next(
        (row for row in audit_a if str(row.get("action") or "") == "owner_request_approved"),
        None,
    )
    _assert(approved_log is not None, "approve: terminal audit row not found")
    approved_payload = _safe_json(str(approved_log.get("payload_json") or ""))
    _assert(int(approved_payload.get("owner_id") or 0) == owner_a_id, f"approve payload owner_id mismatch: {approved_payload}")
    _assert(
        int(approved_payload.get("owner_tg_user_id") or 0) == int(owner_a_row.get("tg_user_id") or 0),
        f"approve payload owner_tg_user_id mismatch: {approved_payload}",
    )

    # Reject branch.
    created_b = await service.register_new_business(
        tg_user_id=720000 + stamp % 10000,
        service_id=service_id,
        place_name=f"Moderation Audit Reject {stamp}",
        description="reject branch",
        address="addr-b",
    )
    owner_b = created_b.get("owner") or {}
    place_b = created_b.get("place") or {}
    owner_b_id = int(owner_b.get("id") or 0)
    place_b_id = int(place_b.get("id") or 0)
    _assert(owner_b_id > 0 and place_b_id > 0, f"invalid reject branch objects: {created_b}")

    rejected = await service.reject_owner_request(admin_id, owner_b_id)
    _assert(str(rejected.get("status") or "") == "rejected", f"reject status mismatch: {rejected}")

    owner_b_row = await repo.get_owner_request(owner_b_id)
    place_b_row = await repo.get_place(place_b_id)
    _assert(owner_b_row is not None, "reject: owner row missing")
    _assert(place_b_row is not None, "reject: place row missing")
    _assert(str(owner_b_row.get("status") or "") == "rejected", f"reject: owner status mismatch: {owner_b_row}")
    _assert(int(place_b_row.get("is_published") or 0) == 0, f"reject: place must remain unpublished: {place_b_row}")
    _assert(int(place_b_row.get("business_enabled") or 0) == 0, f"reject: business_enabled must stay 0: {place_b_row}")

    audit_b = await repo.list_business_audit_logs(limit=100, offset=0, place_id=place_b_id)
    actions_b = [str(row.get("action") or "") for row in audit_b]
    _assert("owner_request_created" in actions_b, f"reject: missing owner_request_created audit: {actions_b}")
    _assert("owner_request_rejected" in actions_b, f"reject: missing owner_request_rejected audit: {actions_b}")
    rejected_log = next(
        (row for row in audit_b if str(row.get("action") or "") == "owner_request_rejected"),
        None,
    )
    _assert(rejected_log is not None, "reject: terminal audit row not found")
    rejected_payload = _safe_json(str(rejected_log.get("payload_json") or ""))
    _assert(int(rejected_payload.get("owner_id") or 0) == owner_b_id, f"reject payload owner_id mismatch: {rejected_payload}")
    _assert(
        int(rejected_payload.get("owner_tg_user_id") or 0) == int(owner_b_row.get("tg_user_id") or 0),
        f"reject payload owner_tg_user_id mismatch: {rejected_payload}",
    )


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-moderation-audit-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "700001")
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: business moderation audit smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
