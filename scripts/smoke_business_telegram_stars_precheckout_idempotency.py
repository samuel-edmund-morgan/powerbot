#!/usr/bin/env python3
"""
Smoke test for Telegram Stars pre-checkout idempotency.

Goal:
- duplicate `validate_telegram_stars_pre_checkout(...)` calls for the same
  invoice payload are safe and do not duplicate canonical `pre_checkout_ok`
  events in DB.
- subsequent successful payment still applies once (idempotent duplicate
  successful_payment with same charge id is ignored).

Run:
  python3 scripts/smoke_business_telegram_stars_precheckout_idempotency.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
import time
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
            (1, "Stars Idempotency Place", "Desc", "Addr", "stars idem"),
        )
        place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        tg_user_id = 11031
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
    _assert(str(intent.get("provider") or "") == "telegram_stars", f"unexpected provider: {intent}")
    invoice_payload = str(intent.get("invoice_payload") or "")
    _assert(invoice_payload != "", "invoice_payload is required")

    # pre-checkout #1
    first = await service.validate_telegram_stars_pre_checkout(
        tg_user_id=int(tg_user_id),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        pre_checkout_query_id="smoke-pc-1",
    )
    _assert(int(first.get("place_id") or 0) == int(place_id), f"pre-checkout #1 failed: {first}")

    # pre-checkout #2 (duplicate for same payload/intention) - must be safe.
    second = await service.validate_telegram_stars_pre_checkout(
        tg_user_id=int(tg_user_id),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        pre_checkout_query_id="smoke-pc-2",
    )
    _assert(int(second.get("place_id") or 0) == int(place_id), f"pre-checkout #2 failed: {second}")

    charge_id = f"tg_pc_idem_{int(time.time())}"
    success = await service.apply_telegram_stars_successful_payment(
        tg_user_id=int(tg_user_id),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        subscription_expiration_date=None,
        is_recurring=True,
        is_first_recurring=True,
        telegram_payment_charge_id=charge_id,
        provider_payment_charge_id="provider-pc-idem",
        raw_payload_json=None,
    )
    _assert(bool(success.get("applied")), f"success should apply once: {success}")

    duplicate_success = await service.apply_telegram_stars_successful_payment(
        tg_user_id=int(tg_user_id),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        subscription_expiration_date=None,
        is_recurring=True,
        is_first_recurring=False,
        telegram_payment_charge_id=charge_id,
        provider_payment_charge_id="provider-pc-idem",
        raw_payload_json=None,
    )
    _assert(not bool(duplicate_success.get("applied")), f"duplicate success must not apply: {duplicate_success}")
    _assert(bool(duplicate_success.get("duplicate")), f"duplicate success flag missing: {duplicate_success}")

    sub = await repo.ensure_subscription(int(place_id))
    _assert(str(sub.get("tier") or "") == "light", f"tier mismatch after success: {sub}")
    _assert(str(sub.get("status") or "") == "active", f"status mismatch after success: {sub}")

    place = await repo.get_place(int(place_id))
    _assert(int(place.get("is_verified") or 0) == 1, f"is_verified mismatch: {place}")
    _assert(str(place.get("verified_tier") or "") == "light", f"verified tier mismatch: {place}")

    import aiosqlite  # noqa: WPS433

    async with aiosqlite.connect(os.environ["DB_PATH"]) as db:
        async with db.execute(
            """
            SELECT event_type, external_payment_id, COUNT(*)
              FROM business_payment_events
             WHERE provider = 'telegram_stars'
               AND event_type IN ('invoice_created', 'pre_checkout_ok', 'payment_succeeded')
             GROUP BY event_type, external_payment_id
             ORDER BY event_type, external_payment_id
            """
        ) as cur:
            rows = await cur.fetchall()

    grouped = {(str(r[0]), str(r[1])): int(r[2]) for r in rows}
    _assert(any(key[0] == "invoice_created" for key in grouped), f"invoice_created missing: {grouped}")
    pre_checkout_rows = [value for (etype, _), value in grouped.items() if etype == "pre_checkout_ok"]
    _assert(len(pre_checkout_rows) == 1 and pre_checkout_rows[0] == 1, f"pre_checkout idempotency broken: {grouped}")
    success_rows = [value for (etype, ext_id), value in grouped.items() if etype == "payment_succeeded" and ext_id == charge_id]
    _assert(len(success_rows) == 1 and success_rows[0] == 1, f"success idempotency broken: {grouped}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-stars-precheckout-idem-"))
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
        print("OK: business telegram stars pre-checkout idempotency smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
