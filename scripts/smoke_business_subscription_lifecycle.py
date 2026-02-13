#!/usr/bin/env python3
"""
Smoke test for business subscription lifecycle reconciliation:
- active paid + expired -> past_due (verified off)
- past_due paid + grace expired -> free/inactive (verified off)
- second reconcile run is idempotent (no additional changes)

Run:
  python3 scripts/smoke_business_subscription_lifecycle.py
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


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _setup_temp_db(db_path: Path) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("Кав'ярні",))

        # place 1: active paid but expired => should become past_due
        conn.execute(
            """
            INSERT INTO places(
                service_id, name, description, address, keywords,
                is_published, is_verified, verified_tier, verified_until, business_enabled
            ) VALUES(1, 'Lifecycle Place 1', 'Desc', 'Addr', 'lc1', 1, 1, 'light', ?, 1)
            """,
            (_iso(now - timedelta(days=1)),),
        )
        place_active_expired = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            """
            INSERT INTO business_subscriptions(place_id, tier, status, starts_at, expires_at, created_at, updated_at)
            VALUES(?, 'light', 'active', ?, ?, ?, ?)
            """,
            (
                place_active_expired,
                _iso(now - timedelta(days=31)),
                _iso(now - timedelta(days=1)),
                _iso(now - timedelta(days=31)),
                _iso(now - timedelta(days=1)),
            ),
        )

        # place 2: past_due paid and grace already expired => should become free/inactive
        conn.execute(
            """
            INSERT INTO places(
                service_id, name, description, address, keywords,
                is_published, is_verified, verified_tier, verified_until, business_enabled
            ) VALUES(1, 'Lifecycle Place 2', 'Desc', 'Addr', 'lc2', 1, 0, NULL, NULL, 1)
            """
        )
        place_past_due = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            """
            INSERT INTO business_subscriptions(place_id, tier, status, starts_at, expires_at, created_at, updated_at)
            VALUES(?, 'pro', 'past_due', ?, ?, ?, ?)
            """,
            (
                place_past_due,
                _iso(now - timedelta(days=40)),
                _iso(now - timedelta(days=10)),
                _iso(now - timedelta(days=40)),
                _iso(now - timedelta(days=10)),
            ),
        )

        conn.commit()
        return place_active_expired, place_past_due
    finally:
        conn.close()


async def _run_checks(place_active_expired: int, place_past_due: int) -> None:
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

    result = await service.reconcile_subscription_states(grace_days=3)
    _assert(int(result.get("changed_active_to_past_due") or 0) == 1, f"unexpected result: {result}")
    _assert(int(result.get("changed_past_due_to_free") or 0) == 1, f"unexpected result: {result}")

    sub1 = await repo.ensure_subscription(int(place_active_expired))
    _assert(str(sub1.get("tier") or "") == "light", f"place1 tier mismatch: {sub1}")
    _assert(str(sub1.get("status") or "") == "past_due", f"place1 status mismatch: {sub1}")
    _assert(bool(sub1.get("expires_at")), f"place1 expires_at must be preserved: {sub1}")

    place1 = await repo.get_place(int(place_active_expired))
    _assert(int(place1.get("is_verified") or 0) == 0, f"place1 is_verified mismatch: {place1}")
    _assert(place1.get("verified_tier") in (None, ""), f"place1 verified_tier mismatch: {place1}")

    sub2 = await repo.ensure_subscription(int(place_past_due))
    _assert(str(sub2.get("tier") or "") == "free", f"place2 tier mismatch: {sub2}")
    _assert(str(sub2.get("status") or "") == "inactive", f"place2 status mismatch: {sub2}")
    _assert(sub2.get("expires_at") in (None, ""), f"place2 expires_at mismatch: {sub2}")

    place2 = await repo.get_place(int(place_past_due))
    _assert(int(place2.get("is_verified") or 0) == 0, f"place2 is_verified mismatch: {place2}")
    _assert(place2.get("verified_tier") in (None, ""), f"place2 verified_tier mismatch: {place2}")

    import aiosqlite  # noqa: WPS433

    async with aiosqlite.connect(os.environ["DB_PATH"]) as db:
        async with db.execute(
            """
            SELECT action, COUNT(*)
              FROM business_audit_log
             WHERE action IN ('subscription_expired_past_due', 'subscription_past_due_to_free')
             GROUP BY action
            """
        ) as cur:
            rows = await cur.fetchall()
    counts = {str(action): int(count) for action, count in rows}
    _assert(int(counts.get("subscription_expired_past_due", 0)) == 1, f"audit mismatch: {counts}")
    _assert(int(counts.get("subscription_past_due_to_free", 0)) == 1, f"audit mismatch: {counts}")

    second = await service.reconcile_subscription_states(grace_days=3)
    _assert(int(second.get("total_changed") or 0) == 0, f"reconcile must be idempotent: {second}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-biz-lifecycle-"))
    try:
        db_path = tmpdir / "state.db"
        place_active_expired, place_past_due = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "1")
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks(place_active_expired, place_past_due))
        print("OK: business subscription lifecycle smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
