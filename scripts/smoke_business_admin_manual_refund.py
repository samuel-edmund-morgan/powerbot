#!/usr/bin/env python3
"""
Smoke test: admin manual refund fallback (Telegram Stars).

Validates:
- admin_mark_payment_refund(event_id) creates canonical refund event (idempotent)
- refund immediately revokes entitlement (tier -> free, status -> inactive, verified -> off)

Run:
  python3 scripts/smoke_business_admin_manual_refund.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
import time
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _setup_temp_db(db_path: Path) -> tuple[int, int]:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("Кав'ярні",))
        conn.execute(
            """
            INSERT INTO places(
                service_id, name, description, address, keywords,
                is_published, is_verified, verified_tier, verified_until, business_enabled
            ) VALUES(?, ?, ?, ?, ?, 1, 0, NULL, NULL, 0)
            """,
            (1, "Refund Admin Place", "Desc", "Addr", "refund admin"),
        )
        place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        tg_user_id = 12001
        created_at = _now_iso()
        conn.execute(
            """
            INSERT INTO business_owners(
                place_id, tg_user_id, role, status, created_at, approved_at, approved_by
            ) VALUES(?, ?, 'owner', 'approved', ?, ?, ?)
            """,
            (place_id, tg_user_id, created_at, created_at, 1),
        )
        conn.commit()
        return place_id, tg_user_id
    finally:
        conn.close()


async def _run_checks(place_id: int, tg_user_id: int) -> None:
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

    intent = await service.create_payment_intent(
        tg_user_id=int(tg_user_id),
        place_id=int(place_id),
        tier="light",
        source="plans",
    )
    invoice_payload = str(intent.get("invoice_payload") or "")
    _assert(invoice_payload != "", "invoice_payload missing for telegram_stars intent")

    await service.validate_telegram_stars_pre_checkout(
        tg_user_id=int(tg_user_id),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        pre_checkout_query_id="smoke-refund-admin-precheckout",
    )

    expiration_unix = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
    charge_id = f"tg_charge_refund_admin_{int(time.time())}"
    success = await service.apply_telegram_stars_successful_payment(
        tg_user_id=int(tg_user_id),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        subscription_expiration_date=expiration_unix,
        is_recurring=True,
        is_first_recurring=True,
        telegram_payment_charge_id=charge_id,
        provider_payment_charge_id="provider-refund-admin",
        raw_payload_json=None,
    )
    _assert(bool(success.get("applied")), f"payment must be applied: {success}")

    events = await repo.get_payment_events_by_external_id(
        provider="telegram_stars",
        external_payment_id=charge_id,
    )
    succeeded = next((e for e in events if str(e.get("event_type") or "") == "payment_succeeded"), None)
    _assert(succeeded is not None, f"payment_succeeded event not found: {events}")
    event_id = int(succeeded.get("id") or 0)
    _assert(event_id > 0, f"invalid payment event id: {succeeded}")

    # Admin refund.
    admin_id = 1
    refund = await service.admin_mark_payment_refund(admin_id, event_id=event_id)
    _assert(bool(refund.get("applied")), f"refund must be applied: {refund}")
    _assert(not bool(refund.get("duplicate")), f"refund must not be duplicate on first apply: {refund}")

    sub_after = await repo.ensure_subscription(int(place_id))
    _assert(str(sub_after.get("tier")) == "free", f"tier not revoked: {sub_after}")
    _assert(str(sub_after.get("status")) == "inactive", f"status not revoked: {sub_after}")

    place = await repo.get_place(int(place_id))
    _assert(int(place.get("is_verified") or 0) == 0, f"is_verified not revoked: {place}")
    _assert(place.get("verified_tier") is None, f"verified_tier not cleared: {place}")
    _assert(place.get("verified_until") is None, f"verified_until not cleared: {place}")

    # Idempotency: second refund should be duplicate.
    dup = await service.admin_mark_payment_refund(admin_id, event_id=event_id)
    _assert(not bool(dup.get("applied")), f"duplicate refund must not be applied: {dup}")
    _assert(bool(dup.get("duplicate")), f"duplicate flag missing: {dup}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-admin-refund-"))
    try:
        db_path = tmpdir / "state.db"
        place_id, tg_user_id = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "1")
        os.environ["BUSINESS_PAYMENT_PROVIDER"] = "telegram_stars"
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks(place_id=place_id, tg_user_id=tg_user_id))
        print("OK: business admin manual refund smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()

