#!/usr/bin/env python3
"""
Smoke test: paid-like purge on downgrade to Free.

Validates three downgrade paths:
1) owner cancel auto-renew + expiry reconcile: paid(canceled) -> free
2) admin forced downgrade: paid -> free
3) maintenance downgrade: past_due -> free

For each case, likes added during paid window must be removed,
while likes outside paid window must remain.
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

ADMIN_ID = 42
OWNER_ID = 5001


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _setup_temp_db(db_path: Path) -> tuple[int, int, int]:
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("Кав'ярні",))

        conn.execute(
            """
            INSERT INTO places(service_id, name, description, address, keywords, is_published, business_enabled)
            VALUES(1, 'Purge Owner', 'Desc', 'Addr', 'p1', 1, 1)
            """
        )
        place_owner = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            """
            INSERT INTO places(service_id, name, description, address, keywords, is_published, business_enabled)
            VALUES(1, 'Purge Admin', 'Desc', 'Addr', 'p2', 1, 1)
            """
        )
        place_admin = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            """
            INSERT INTO places(service_id, name, description, address, keywords, is_published, business_enabled)
            VALUES(1, 'Purge Reconcile', 'Desc', 'Addr', 'p3', 1, 1)
            """
        )
        place_reconcile = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        # Approved owner for owner-managed scenarios.
        for place_id in (place_owner, place_reconcile):
            conn.execute(
                """
                INSERT INTO business_owners(place_id, tg_user_id, role, status, created_at, approved_at, approved_by)
                VALUES(?, ?, 'owner', 'approved', ?, ?, ?)
                """,
                (place_id, OWNER_ID, _iso(now), _iso(now), ADMIN_ID),
            )

        conn.commit()
        return place_owner, place_admin, place_reconcile
    finally:
        conn.close()


def _insert_like(db_path: Path, place_id: int, chat_id: int, liked_at: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO place_likes(place_id, chat_id, liked_at) VALUES(?, ?, ?)",
            (int(place_id), int(chat_id), str(liked_at)),
        )
        conn.commit()
    finally:
        conn.close()


def _list_like_chat_ids(db_path: Path, place_id: int) -> list[int]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT chat_id FROM place_likes WHERE place_id = ? ORDER BY chat_id",
            (int(place_id),),
        ).fetchall()
        return [int(row[0]) for row in rows]
    finally:
        conn.close()


def _periods_state(db_path: Path, place_id: int) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT tier, started_at, paid_until, source, closed_at, close_reason, purge_processed_at
              FROM business_subscription_periods
             WHERE place_id = ?
             ORDER BY id ASC
            """,
            (int(place_id),),
        ).fetchall()
        return rows
    finally:
        conn.close()


def _set_paid_window(db_path: Path, place_id: int, *, starts_at: str, paid_until: str, status: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        now_iso = _iso(datetime.now(timezone.utc))
        conn.execute(
            """
            UPDATE business_subscriptions
               SET tier = COALESCE(NULLIF(tier, ''), 'light'),
                   status = ?,
                   starts_at = ?,
                   expires_at = ?,
                   updated_at = ?
             WHERE place_id = ?
            """,
            (str(status), str(starts_at), str(paid_until), now_iso, int(place_id)),
        )
        conn.execute(
            """
            UPDATE business_subscription_periods
               SET started_at = ?,
                   paid_until = ?,
                   updated_at = ?
             WHERE place_id = ?
               AND purge_processed_at IS NULL
            """,
            (str(starts_at), str(paid_until), now_iso, int(place_id)),
        )
        conn.commit()
    finally:
        conn.close()


async def _run_checks(db_path: Path, place_owner: int, place_admin: int, place_reconcile: int) -> None:
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
    now = datetime.now(timezone.utc)

    # 1) Owner cancel auto-renew + expiry -> free (reconcile)
    await service.change_subscription_tier(OWNER_ID, int(place_owner), "light")
    owner_start = now - timedelta(days=3)
    owner_until = now + timedelta(days=3)
    _set_paid_window(
        db_path,
        place_owner,
        starts_at=_iso(owner_start),
        paid_until=_iso(owner_until),
        status="active",
    )
    _insert_like(db_path, place_owner, 101, _iso(owner_start - timedelta(hours=2)))  # keep
    _insert_like(db_path, place_owner, 102, _iso(owner_start + timedelta(hours=2)))  # purge

    canceled_sub = await service.cancel_subscription_auto_renew(OWNER_ID, int(place_owner))
    _assert(str(canceled_sub.get("tier") or "") == "light", f"owner cancel tier mismatch: {canceled_sub}")
    _assert(str(canceled_sub.get("status") or "") == "canceled", f"owner cancel status mismatch: {canceled_sub}")

    # Entitlement must remain active until expiry (owner can still edit).
    await service.update_place_field(
        OWNER_ID,
        int(place_owner),
        "description",
        "Owner still can edit while canceled but not expired",
    )

    # Immediate free downgrade must be blocked for active paid entitlement.
    try:
        await service.change_subscription_tier(OWNER_ID, int(place_owner), "free")
        raise AssertionError("Expected ValidationError for immediate free after cancel")
    except ValidationError:
        pass

    # Simulate natural period end, then lifecycle reconcile should downgrade and purge.
    owner_expired_until = now - timedelta(hours=2)
    _set_paid_window(
        db_path,
        place_owner,
        starts_at=_iso(owner_start),
        paid_until=_iso(owner_expired_until),
        status="canceled",
    )
    owner_reconcile = await service.reconcile_subscription_states(grace_days=0)
    _assert(int(owner_reconcile.get("changed_canceled_to_free") or 0) >= 1, f"owner reconcile mismatch: {owner_reconcile}")

    likes_owner = _list_like_chat_ids(db_path, place_owner)
    _assert(likes_owner == [101], f"owner downgrade likes mismatch: {likes_owner}")
    periods_owner = _periods_state(db_path, place_owner)
    _assert(len(periods_owner) >= 1, f"owner periods missing: {periods_owner}")
    _assert(periods_owner[-1][6] is not None, f"owner purge_processed_at missing: {periods_owner}")

    # 2) Admin paid -> free
    await service.admin_set_subscription_tier(ADMIN_ID, place_id=int(place_admin), tier="pro", months=1)
    admin_start = now - timedelta(days=2)
    admin_until = now + timedelta(days=4)
    _set_paid_window(
        db_path,
        place_admin,
        starts_at=_iso(admin_start),
        paid_until=_iso(admin_until),
        status="active",
    )
    _insert_like(db_path, place_admin, 201, _iso(admin_start - timedelta(hours=2)))  # keep
    _insert_like(db_path, place_admin, 202, _iso(admin_start + timedelta(hours=2)))  # purge

    await service.admin_set_subscription_tier(ADMIN_ID, place_id=int(place_admin), tier="free", months=1)
    likes_admin = _list_like_chat_ids(db_path, place_admin)
    _assert(likes_admin == [201], f"admin downgrade likes mismatch: {likes_admin}")
    periods_admin = _periods_state(db_path, place_admin)
    _assert(len(periods_admin) >= 1, f"admin periods missing: {periods_admin}")
    _assert(periods_admin[-1][6] is not None, f"admin purge_processed_at missing: {periods_admin}")

    # 3) Maintenance past_due -> free
    await service.change_subscription_tier(OWNER_ID, int(place_reconcile), "light")
    reconcile_start = now - timedelta(days=20)
    reconcile_until = now - timedelta(days=10)
    _set_paid_window(
        db_path,
        place_reconcile,
        starts_at=_iso(reconcile_start),
        paid_until=_iso(reconcile_until),
        status="past_due",
    )

    _insert_like(db_path, place_reconcile, 301, _iso(reconcile_start + timedelta(hours=1)))  # purge
    _insert_like(db_path, place_reconcile, 302, _iso(reconcile_until + timedelta(hours=1)))  # keep

    result = await service.reconcile_subscription_states(grace_days=0)
    _assert(int(result.get("changed_past_due_to_free") or 0) >= 1, f"reconcile result mismatch: {result}")

    likes_reconcile = _list_like_chat_ids(db_path, place_reconcile)
    _assert(likes_reconcile == [302], f"reconcile downgrade likes mismatch: {likes_reconcile}")
    periods_reconcile = _periods_state(db_path, place_reconcile)
    _assert(len(periods_reconcile) >= 1, f"reconcile periods missing: {periods_reconcile}")
    _assert(periods_reconcile[-1][6] is not None, f"reconcile purge_processed_at missing: {periods_reconcile}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-biz-purge-"))
    try:
        db_path = tmpdir / "state.db"
        place_owner, place_admin, place_reconcile = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ["ADMIN_IDS"] = str(ADMIN_ID)
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks(db_path, place_owner, place_admin, place_reconcile))
        print("OK: business paid-like purge smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
