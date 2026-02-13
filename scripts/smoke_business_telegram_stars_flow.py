#!/usr/bin/env python3
"""
Smoke test for Telegram Stars provider flow in business billing:
- create intent (telegram_stars payload)
- pre_checkout validation writes canonical pre_checkout_ok event
- successful payment activates subscription/verified flags
- repeated successful payment with same charge id is idempotent

Run:
  python3 scripts/smoke_business_telegram_stars_flow.py
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
        schema = SCHEMA_SQL.read_text(encoding="utf-8")
        conn.executescript(schema)
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("Кав'ярні",))

        conn.execute(
            """
            INSERT INTO places(
                service_id, name, description, address, keywords,
                is_published, is_verified, verified_tier, verified_until, business_enabled
            ) VALUES(?, ?, ?, ?, ?, 1, 0, NULL, NULL, 0)
            """,
            (1, "Stars Place", "Desc", "Addr", "stars"),
        )
        place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        tg_user_id = 11001
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

    from business.payments import decode_telegram_stars_payload  # noqa: WPS433
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
    _assert(str(intent.get("provider")) == "telegram_stars", f"unexpected provider: {intent}")
    invoice_payload = str(intent.get("invoice_payload") or "")
    _assert(invoice_payload != "", "telegram stars intent must include invoice_payload")
    payload = decode_telegram_stars_payload(invoice_payload)
    _assert(payload is not None, "failed to decode invoice_payload")
    _assert(int(payload.place_id) == int(place_id), "invoice payload place_id mismatch")
    _assert(int(payload.tg_user_id) == int(tg_user_id), "invoice payload tg_user_id mismatch")
    _assert(str(payload.tier) == "light", "invoice payload tier mismatch")

    pre = await service.validate_telegram_stars_pre_checkout(
        tg_user_id=int(tg_user_id),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        pre_checkout_query_id="smoke-precheckout",
    )
    _assert(int(pre.get("place_id") or 0) == int(place_id), "pre_checkout place_id mismatch")
    _assert(str(pre.get("tier") or "") == "light", "pre_checkout tier mismatch")
    _assert(int(pre.get("amount_stars") or 0) == 1000, "pre_checkout amount mismatch")

    # Expiration in the future (Telegram sends unix timestamp, UTC).
    expiration_unix = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
    charge_id = f"tg_charge_smoke_{int(time.time())}"
    success = await service.apply_telegram_stars_successful_payment(
        tg_user_id=int(tg_user_id),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        subscription_expiration_date=expiration_unix,
        is_recurring=True,
        is_first_recurring=True,
        telegram_payment_charge_id=charge_id,
        provider_payment_charge_id="provider-smoke-charge",
        raw_payload_json=None,
    )
    _assert(bool(success.get("applied")), "successful payment must be applied")
    _assert(not bool(success.get("duplicate")), "successful payment must not be duplicate on first apply")

    sub = await repo.ensure_subscription(int(place_id))
    _assert(str(sub.get("tier")) == "light", f"subscription tier mismatch: {sub}")
    _assert(str(sub.get("status")) == "active", f"subscription status mismatch: {sub}")
    _assert(bool(sub.get("expires_at")), f"subscription expires_at missing: {sub}")

    place = await repo.get_place(int(place_id))
    _assert(int(place.get("is_verified") or 0) == 1, f"is_verified mismatch: {place}")
    _assert(str(place.get("verified_tier") or "") == "light", f"verified_tier mismatch: {place}")
    _assert(bool(place.get("verified_until")), f"verified_until missing: {place}")

    duplicate = await service.apply_telegram_stars_successful_payment(
        tg_user_id=int(tg_user_id),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        subscription_expiration_date=expiration_unix,
        is_recurring=True,
        is_first_recurring=False,
        telegram_payment_charge_id=charge_id,
        provider_payment_charge_id="provider-smoke-charge",
        raw_payload_json=None,
    )
    _assert(not bool(duplicate.get("applied")), "duplicate payment must not be re-applied")
    _assert(bool(duplicate.get("duplicate")), "duplicate flag must be true")

    import aiosqlite  # noqa: WPS433

    async with aiosqlite.connect(os.environ["DB_PATH"]) as db:
        async with db.execute(
            """
            SELECT provider, external_payment_id, event_type
              FROM business_payment_events
             WHERE provider = 'telegram_stars'
             ORDER BY id
            """
        ) as cur:
            rows = await cur.fetchall()
    events = [(str(r[0]), str(r[1]), str(r[2])) for r in rows]
    _assert(any(evt[2] == "invoice_created" for evt in events), f"invoice_created missing: {events}")
    _assert(any(evt[2] == "pre_checkout_ok" for evt in events), f"pre_checkout_ok missing: {events}")
    _assert(
        len([evt for evt in events if evt[2] == "payment_succeeded" and evt[1] == charge_id]) == 1,
        f"payment_succeeded idempotency broken: {events}",
    )


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-stars-"))
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
        print("OK: business telegram stars flow smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
