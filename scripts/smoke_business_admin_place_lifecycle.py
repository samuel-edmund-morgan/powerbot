#!/usr/bin/env python3
"""
Smoke test: admin place lifecycle operations.

Validates:
- admin can create and rename service
- admin can create place as unpublished draft
- publish/unpublish toggles work
- published place cannot be deleted as draft
- unpublished draft can be deleted
- audit log contains expected actions

Run:
  python3 scripts/smoke_business_admin_place_lifecycle.py
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
        conn.execute(
            "INSERT INTO buildings(id, name, address, has_sensor, sensor_count) VALUES(1, 'Ньюкасл', '24-в', 0, 0)"
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

    from business.repository import BusinessRepository  # noqa: WPS433
    from business.service import BusinessCabinetService, ValidationError  # noqa: WPS433

    repo = BusinessRepository()
    service = BusinessCabinetService(repository=repo)

    admin_id = 1
    service.admin_ids.add(admin_id)

    stamp = int(time.time())
    base_name = f"Smoke Lifecycle {stamp}"
    renamed_name = f"{base_name} Renamed"

    created_service = await service.admin_create_service(admin_id, base_name)
    service_id = int(created_service.get("id") or 0)
    _assert(service_id > 0, f"invalid created service: {created_service}")

    renamed = await service.admin_rename_service(admin_id, service_id, renamed_name)
    _assert(str(renamed.get("name") or "") == renamed_name, f"rename failed: {renamed}")

    place = await service.admin_create_place(
        admin_id,
        service_id=service_id,
        name=f"Smoke Place {stamp}",
        description="Smoke description",
        building_id=1,
        address_details="-1 поверх",
        is_published=0,
    )
    place_id = int(place.get("id") or 0)
    _assert(place_id > 0, f"invalid place: {place}")
    _assert(int(place.get("is_published") or 0) == 0, f"new place must be unpublished: {place}")
    _assert(
        "Ньюкасл (24-в), -1 поверх" in str(place.get("address") or ""),
        f"address must use building label + details: {place}",
    )

    published = await service.set_place_published(admin_id, place_id, is_published=1)
    _assert(int(published.get("is_published") or 0) == 1, f"publish failed: {published}")

    try:
        await service.delete_place_draft(admin_id, place_id)
    except ValidationError:
        pass
    else:
        raise AssertionError("published place must not be deletable as draft")

    hidden = await service.set_place_published(admin_id, place_id, is_published=0)
    _assert(int(hidden.get("is_published") or 0) == 0, f"unpublish failed: {hidden}")

    deleted_snapshot = await service.delete_place_draft(admin_id, place_id)
    _assert(int(deleted_snapshot.get("id") or 0) == place_id, f"delete result mismatch: {deleted_snapshot}")

    place_after = await repo.get_place(place_id)
    _assert(place_after is None, "draft place must be deleted")

    import aiosqlite  # noqa: WPS433

    async with aiosqlite.connect(os.environ["DB_PATH"]) as db:
        async with db.execute(
            """
            SELECT action, COUNT(*)
              FROM business_audit_log
             WHERE place_id = ?
             GROUP BY action
            """,
            (place_id,),
        ) as cur:
            rows = await cur.fetchall()

    counts = {str(action): int(count) for action, count in rows}
    _assert(counts.get("admin_place_created", 0) == 1, f"admin_place_created missing: {counts}")
    _assert(counts.get("place_publish_toggled", 0) >= 2, f"place_publish_toggled count invalid: {counts}")
    _assert(counts.get("place_draft_deleted", 0) == 1, f"place_draft_deleted missing: {counts}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-admin-place-lifecycle-"))
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
        print("OK: business admin place lifecycle smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
