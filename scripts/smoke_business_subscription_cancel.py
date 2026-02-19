#!/usr/bin/env python3
"""
Smoke test: owner cancel flow keeps entitlement until expiry.

Checks:
- paid active -> cancel => status=canceled, paid tier preserved
- owner can still edit while not expired (basic + business profile + contact)
- verified flags remain active during canceled paid window
- immediate free downgrade is blocked while entitlement active
- after expires_at, reconcile downgrades canceled -> free/inactive and disables verified

Run:
  python3 scripts/smoke_business_subscription_cancel.py
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
OWNER_ID = 9001


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _setup_temp_db(db_path: Path) -> int:
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("Кав'ярні",))
        conn.execute(
            """
            INSERT INTO places(
                service_id, name, description, address, keywords,
                is_published, is_verified, verified_tier, verified_until, business_enabled
            ) VALUES(1, 'Cancel Place', 'Desc', 'Addr', 'cancel', 1, 0, NULL, NULL, 1)
            """
        )
        place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            """
            INSERT INTO business_owners(place_id, tg_user_id, role, status, created_at, approved_at, approved_by)
            VALUES(?, ?, 'owner', 'approved', ?, ?, ?)
            """,
            (place_id, OWNER_ID, _iso(now), _iso(now), ADMIN_ID),
        )
        conn.commit()
        return place_id
    finally:
        conn.close()


def _force_canceled_expired(db_path: Path, place_id: int) -> None:
    now = datetime.now(timezone.utc)
    expired_at = _iso(now - timedelta(hours=1))
    started_at = _iso(now - timedelta(days=30))
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            UPDATE business_subscriptions
               SET tier='light',
                   status='canceled',
                   starts_at=?,
                   expires_at=?,
                   updated_at=?
             WHERE place_id=?
            """,
            (started_at, expired_at, _iso(now), int(place_id)),
        )
        conn.execute(
            """
            UPDATE business_subscription_periods
               SET started_at=?,
                   paid_until=?,
                   updated_at=?
             WHERE place_id=?
               AND purge_processed_at IS NULL
            """,
            (started_at, expired_at, _iso(now), int(place_id)),
        )
        conn.commit()
    finally:
        conn.close()


async def _run_checks(db_path: Path, place_id: int) -> None:
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

    await service.change_subscription_tier(OWNER_ID, int(place_id), "light")
    paid = await repo.ensure_subscription(int(place_id))
    _assert(str(paid.get("tier") or "") == "light", f"paid tier mismatch: {paid}")
    _assert(str(paid.get("status") or "") == "active", f"paid status mismatch: {paid}")
    _assert(bool(paid.get("expires_at")), f"paid expires_at missing: {paid}")

    canceled = await service.cancel_subscription_auto_renew(OWNER_ID, int(place_id))
    _assert(str(canceled.get("tier") or "") == "light", f"cancel tier mismatch: {canceled}")
    _assert(str(canceled.get("status") or "") == "canceled", f"cancel status mismatch: {canceled}")

    # Still editable while entitlement is active.
    updated = await service.update_place_field(
        OWNER_ID,
        int(place_id),
        "description",
        "Updated while canceled and still active",
    )
    _assert("Updated while canceled" in str(updated.get("description") or ""), f"edit failed after cancel: {updated}")

    # Business profile edits must also stay available while canceled but not expired.
    updated_profile = await service.update_place_business_profile_field(
        OWNER_ID,
        int(place_id),
        "opening_hours",
        "09:00-21:00",
    )
    _assert(
        "09:00-21:00" in str(updated_profile.get("opening_hours") or ""),
        f"business profile edit failed after cancel: {updated_profile}",
    )
    updated_contact = await service.update_place_contact(
        OWNER_ID,
        place_id=int(place_id),
        contact_type="chat",
        contact_value="@cancel_place_chat",
    )
    _assert(
        str(updated_contact.get("contact_type") or "") == "chat"
        and str(updated_contact.get("contact_value") or "") == "@cancel_place_chat",
        f"contact edit failed after cancel: {updated_contact}",
    )

    # Verified flags must remain active until expires_at.
    place_during_canceled = await repo.get_place(int(place_id))
    _assert(int(place_during_canceled.get("is_verified") or 0) == 1, f"verified unexpectedly off: {place_during_canceled}")
    _assert(
        str(place_during_canceled.get("verified_tier") or "").strip().lower() == "light",
        f"verified tier mismatch while canceled: {place_during_canceled}",
    )

    # Direct free downgrade must be blocked until paid period ends.
    try:
        await service.change_subscription_tier(OWNER_ID, int(place_id), "free")
        raise AssertionError("Expected ValidationError for immediate free downgrade after cancel")
    except ValidationError:
        pass

    _force_canceled_expired(db_path, int(place_id))
    reconcile = await service.reconcile_subscription_states(grace_days=3)
    _assert(int(reconcile.get("changed_canceled_to_free") or 0) >= 1, f"reconcile mismatch: {reconcile}")

    after = await repo.ensure_subscription(int(place_id))
    _assert(str(after.get("tier") or "") == "free", f"after tier mismatch: {after}")
    _assert(str(after.get("status") or "") == "inactive", f"after status mismatch: {after}")
    _assert(after.get("expires_at") in (None, ""), f"after expires_at mismatch: {after}")

    place = await repo.get_place(int(place_id))
    _assert(int(place.get("is_verified") or 0) == 0, f"verified should be off after expiry: {place}")
    _assert(place.get("verified_tier") in (None, ""), f"verified_tier should be empty: {place}")

    import aiosqlite  # noqa: WPS433

    async with aiosqlite.connect(os.environ["DB_PATH"]) as db:
        async with db.execute(
            """
            SELECT action, COUNT(*)
              FROM business_audit_log
             WHERE action IN ('subscription_cancel_requested', 'subscription_canceled_to_free')
             GROUP BY action
            """
        ) as cur:
            rows = await cur.fetchall()
    counts = {str(action): int(count) for action, count in rows}
    _assert(int(counts.get("subscription_cancel_requested", 0)) == 1, f"audit cancel mismatch: {counts}")
    _assert(int(counts.get("subscription_canceled_to_free", 0)) >= 1, f"audit reconcile mismatch: {counts}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-biz-cancel-"))
    try:
        db_path = tmpdir / "state.db"
        place_id = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ["ADMIN_IDS"] = str(ADMIN_ID)
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks(db_path, place_id))
        print("OK: business subscription cancel smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
