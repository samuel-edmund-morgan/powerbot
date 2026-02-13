#!/usr/bin/env python3
"""
Smoke test for Telegram Stars non-success terminal events:
- cancel -> payment_canceled
- fail -> payment_failed
- refund -> refund

Checks:
- each path routes through canonical apply_payment_event and persists event
- duplicate terminal event with same external id is idempotent
- cancel/fail do not activate subscription
- refund event is persisted/audited without corrupting active entitlement

Run:
  python3 scripts/smoke_business_telegram_stars_terminal_events.py
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


def _setup_temp_db(db_path: Path) -> list[tuple[int, int]]:
    conn = sqlite3.connect(db_path)
    try:
        schema = SCHEMA_SQL.read_text(encoding="utf-8")
        conn.executescript(schema)
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("Кав'ярні",))

        pairs: list[tuple[int, int]] = []
        created_at = _now_iso()
        for idx in range(1, 4):
            conn.execute(
                """
                INSERT INTO places(
                    service_id, name, description, address, keywords,
                    is_published, is_verified, verified_tier, verified_until, business_enabled
                ) VALUES(?, ?, ?, ?, ?, 1, 0, NULL, NULL, 0)
                """,
                (1, f"Stars Terminal {idx}", "Desc", "Addr", f"stars terminal {idx}"),
            )
            place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            tg_user_id = 12100 + idx
            conn.execute(
                """
                INSERT INTO business_owners(
                    place_id, tg_user_id, role, status, created_at, approved_at, approved_by
                ) VALUES(?, ?, 'owner', 'approved', ?, ?, ?)
                """,
                (place_id, tg_user_id, created_at, created_at, 1),
            )
            pairs.append((place_id, tg_user_id))

        conn.commit()
        return pairs
    finally:
        conn.close()


async def _run_checks(pairs: list[tuple[int, int]]) -> None:
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

    # 1) cancel path
    place_cancel, tg_cancel = pairs[0]
    cancel_intent = await service.create_payment_intent(
        tg_user_id=int(tg_cancel),
        place_id=int(place_cancel),
        tier="light",
        source="plans",
    )
    cancel_invoice_payload = str(cancel_intent.get("invoice_payload") or "")
    cancel_charge_id = f"tg_cancel_{int(time.time())}"
    cancel_outcome = await service.apply_telegram_stars_terminal_event(
        tg_user_id=int(tg_cancel),
        invoice_payload=cancel_invoice_payload,
        total_amount=1000,
        currency="XTR",
        terminal_kind="cancel",
        telegram_payment_charge_id=cancel_charge_id,
        provider_payment_charge_id="provider-cancel",
        raw_payload_json=None,
        reason="smoke-cancel",
    )
    _assert(bool(cancel_outcome.get("applied")), f"cancel not applied: {cancel_outcome}")
    cancel_dup = await service.apply_telegram_stars_terminal_event(
        tg_user_id=int(tg_cancel),
        invoice_payload=cancel_invoice_payload,
        total_amount=1000,
        currency="XTR",
        terminal_kind="canceled",
        telegram_payment_charge_id=cancel_charge_id,
        provider_payment_charge_id="provider-cancel",
        raw_payload_json=None,
        reason="smoke-cancel-dup",
    )
    _assert(not bool(cancel_dup.get("applied")), f"cancel duplicate applied: {cancel_dup}")
    _assert(bool(cancel_dup.get("duplicate")), f"cancel duplicate flag missing: {cancel_dup}")
    cancel_sub = await repo.ensure_subscription(int(place_cancel))
    _assert(str(cancel_sub.get("tier")) == "free", f"cancel changed tier: {cancel_sub}")
    _assert(str(cancel_sub.get("status")) == "inactive", f"cancel changed status: {cancel_sub}")

    # 2) fail path
    place_fail, tg_fail = pairs[1]
    fail_intent = await service.create_payment_intent(
        tg_user_id=int(tg_fail),
        place_id=int(place_fail),
        tier="light",
        source="plans",
    )
    fail_invoice_payload = str(fail_intent.get("invoice_payload") or "")
    fail_charge_id = f"tg_fail_{int(time.time())}"
    fail_outcome = await service.apply_telegram_stars_terminal_event(
        tg_user_id=int(tg_fail),
        invoice_payload=fail_invoice_payload,
        total_amount=1000,
        currency="XTR",
        terminal_kind="fail",
        telegram_payment_charge_id=fail_charge_id,
        provider_payment_charge_id="provider-fail",
        raw_payload_json=None,
        reason="smoke-fail",
    )
    _assert(bool(fail_outcome.get("applied")), f"fail not applied: {fail_outcome}")
    fail_dup = await service.apply_telegram_stars_terminal_event(
        tg_user_id=int(tg_fail),
        invoice_payload=fail_invoice_payload,
        total_amount=1000,
        currency="XTR",
        terminal_kind="failed",
        telegram_payment_charge_id=fail_charge_id,
        provider_payment_charge_id="provider-fail",
        raw_payload_json=None,
        reason="smoke-fail-dup",
    )
    _assert(not bool(fail_dup.get("applied")), f"fail duplicate applied: {fail_dup}")
    _assert(bool(fail_dup.get("duplicate")), f"fail duplicate flag missing: {fail_dup}")
    fail_sub = await repo.ensure_subscription(int(place_fail))
    _assert(str(fail_sub.get("tier")) == "free", f"fail changed tier: {fail_sub}")
    _assert(str(fail_sub.get("status")) == "inactive", f"fail changed status: {fail_sub}")

    # 3) refund path (after success, entitlement remains stable by current contract)
    place_refund, tg_refund = pairs[2]
    refund_intent = await service.create_payment_intent(
        tg_user_id=int(tg_refund),
        place_id=int(place_refund),
        tier="light",
        source="plans",
    )
    refund_invoice_payload = str(refund_intent.get("invoice_payload") or "")
    expiration_unix = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
    success_charge_id = f"tg_success_{int(time.time())}"
    success = await service.apply_telegram_stars_successful_payment(
        tg_user_id=int(tg_refund),
        invoice_payload=refund_invoice_payload,
        total_amount=1000,
        currency="XTR",
        subscription_expiration_date=expiration_unix,
        is_recurring=True,
        is_first_recurring=True,
        telegram_payment_charge_id=success_charge_id,
        provider_payment_charge_id="provider-success",
        raw_payload_json=None,
    )
    _assert(bool(success.get("applied")), f"pre-refund success not applied: {success}")

    before_refund = await repo.ensure_subscription(int(place_refund))
    refund_charge_id = f"tg_refund_{int(time.time())}"
    refund_outcome = await service.apply_telegram_stars_terminal_event(
        tg_user_id=int(tg_refund),
        invoice_payload=refund_invoice_payload,
        total_amount=1000,
        currency="XTR",
        terminal_kind="refund",
        telegram_payment_charge_id=refund_charge_id,
        provider_payment_charge_id="provider-refund",
        raw_payload_json=None,
        reason="smoke-refund",
    )
    _assert(bool(refund_outcome.get("applied")), f"refund not applied: {refund_outcome}")
    refund_dup = await service.apply_telegram_stars_terminal_event(
        tg_user_id=int(tg_refund),
        invoice_payload=refund_invoice_payload,
        total_amount=1000,
        currency="XTR",
        terminal_kind="refunded",
        telegram_payment_charge_id=refund_charge_id,
        provider_payment_charge_id="provider-refund",
        raw_payload_json=None,
        reason="smoke-refund-dup",
    )
    _assert(not bool(refund_dup.get("applied")), f"refund duplicate applied: {refund_dup}")
    _assert(bool(refund_dup.get("duplicate")), f"refund duplicate flag missing: {refund_dup}")

    after_refund = await repo.ensure_subscription(int(place_refund))
    _assert(str(after_refund.get("tier")) == str(before_refund.get("tier")), f"refund changed tier unexpectedly: {after_refund}")
    _assert(str(after_refund.get("status")) == str(before_refund.get("status")), f"refund changed status unexpectedly: {after_refund}")

    import aiosqlite  # noqa: WPS433

    async with aiosqlite.connect(os.environ["DB_PATH"]) as db:
        async with db.execute(
            """
            SELECT external_payment_id, event_type, COUNT(*)
              FROM business_payment_events
             WHERE provider='telegram_stars'
               AND event_type IN ('payment_canceled', 'payment_failed', 'refund')
             GROUP BY external_payment_id, event_type
             ORDER BY event_type, external_payment_id
            """
        ) as cur:
            rows = await cur.fetchall()
    events = [(str(row[0]), str(row[1]), int(row[2])) for row in rows]
    _assert(any(evt[1] == "payment_canceled" and evt[2] == 1 for evt in events), f"payment_canceled missing: {events}")
    _assert(any(evt[1] == "payment_failed" and evt[2] == 1 for evt in events), f"payment_failed missing: {events}")
    _assert(any(evt[1] == "refund" and evt[2] == 1 for evt in events), f"refund missing: {events}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-stars-terminal-"))
    try:
        db_path = tmpdir / "state.db"
        pairs = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "1")
        os.environ["BUSINESS_PAYMENT_PROVIDER"] = "telegram_stars"
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks(pairs))
        print("OK: business telegram stars terminal-events smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
