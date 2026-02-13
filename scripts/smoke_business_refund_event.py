#!/usr/bin/env python3
"""
Smoke test for canonical `refund` payment event handling.

What it validates:
- `apply_payment_event(..., event_type='refund')` is accepted and persisted.
- duplicate refund for the same provider/external_payment_id is idempotent.
- refund writes audit log entry with contextual payload.
- refund does not silently activate/upgrade subscription state.

Run:
  python3 scripts/smoke_business_refund_event.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
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


def _setup_temp_db(db_path: Path) -> None:
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
            (1, "Refund Place", "Desc", "Addr", "refund place"),
        )
        place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        created_at = _now_iso()
        conn.execute(
            """
            INSERT INTO business_owners(
                place_id, tg_user_id, role, status, created_at, approved_at, approved_by
            ) VALUES(?, ?, 'owner', 'approved', ?, ?, ?)
            """,
            (place_id, 111001, created_at, created_at, 1),
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
    from business.service import BusinessCabinetService  # noqa: WPS433

    repo = BusinessRepository()
    service = BusinessCabinetService(repository=repo)

    # Create active paid subscription first.
    intent = await service.create_mock_payment_intent(
        tg_user_id=111001,
        place_id=1,
        tier="light",
        source="plans",
    )
    intent_id = str(intent["external_payment_id"])
    success = await service.apply_mock_payment_result(
        tg_user_id=111001,
        place_id=1,
        tier="light",
        external_payment_id=intent_id,
        result="success",
    )
    _assert(bool(success.get("applied")), f"payment success was not applied: {success}")

    sub_before = await repo.ensure_subscription(1)
    _assert(str(sub_before.get("tier")) == "light", f"unexpected tier before refund: {sub_before}")
    _assert(str(sub_before.get("status")) == "active", f"unexpected status before refund: {sub_before}")

    refund_outcome = await service.apply_payment_event(
        tg_user_id=111001,
        place_id=1,
        tier="light",
        provider="telegram_stars",
        intent_external_payment_id=intent_id,
        payment_external_id="tg-refund-001",
        event_type="refund",
        amount_stars=1000,
        source="telegram_refund",
        currency="XTR",
        status="processed",
        raw_payload_json='{"source":"smoke_refund"}',
        audit_extra={"refund_reason": "smoke"},
    )
    _assert(bool(refund_outcome.get("applied")), f"refund event not applied: {refund_outcome}")
    _assert(not bool(refund_outcome.get("duplicate")), f"refund event wrongly duplicate: {refund_outcome}")

    duplicate = await service.apply_payment_event(
        tg_user_id=111001,
        place_id=1,
        tier="light",
        provider="telegram_stars",
        intent_external_payment_id=intent_id,
        payment_external_id="tg-refund-001",
        event_type="refund",
        amount_stars=1000,
        source="telegram_refund",
        currency="XTR",
        status="processed",
        raw_payload_json='{"source":"smoke_refund_duplicate"}',
    )
    _assert(not bool(duplicate.get("applied")), f"duplicate refund applied unexpectedly: {duplicate}")
    _assert(bool(duplicate.get("duplicate")), f"duplicate refund flag missing: {duplicate}")

    import aiosqlite  # noqa: WPS433

    async with aiosqlite.connect(os.environ["DB_PATH"]) as db:
        async with db.execute(
            """
            SELECT COUNT(*)
              FROM business_payment_events
             WHERE provider='telegram_stars'
               AND external_payment_id='tg-refund-001'
               AND event_type='refund'
            """
        ) as cur:
            refund_count = int((await cur.fetchone())[0])
        async with db.execute(
            """
            SELECT action, payload_json
              FROM business_audit_log
             WHERE place_id=1 AND action='refund'
             ORDER BY id DESC
             LIMIT 1
            """
        ) as cur:
            audit_row = await cur.fetchone()

    _assert(refund_count == 1, f"unexpected refund event rows: {refund_count}")
    _assert(audit_row is not None, "refund audit log entry is missing")
    _assert("tg-refund-001" in str(audit_row[1]), f"refund audit payload missing external id: {audit_row}")
    _assert("refund_reason" in str(audit_row[1]), f"refund audit payload missing refund_reason: {audit_row}")

    # Current behavior contract: canonical refund event is recorded/audited,
    # while subscription state remains unchanged until explicit entitlement policy is introduced.
    sub_after = await repo.ensure_subscription(1)
    _assert(str(sub_after.get("tier")) == str(sub_before.get("tier")), f"refund changed tier unexpectedly: {sub_after}")
    _assert(str(sub_after.get("status")) == str(sub_before.get("status")), f"refund changed status unexpectedly: {sub_after}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-business-refund-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "1")
        os.environ["BUSINESS_PAYMENT_PROVIDER"] = "mock"
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: business refund event smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
