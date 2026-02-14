#!/usr/bin/env python3
"""
Smoke test: payment-provider parity for success and non-success outcomes.

Goal:
- `mock` and `telegram_stars` must produce equivalent business state
  for:
  - terminal non-success outcomes (`cancel`, `fail`)
  - successful payment (`success`)

Checks:
- non-success: subscription remains `free/inactive`, verified flags disabled
- success: subscription becomes `light/active`, verified flags enabled
- duplicate terminal/success events are idempotent
- canonical events exist for both providers

Run:
  python3 scripts/smoke_business_payment_provider_parity.py
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


def _setup_temp_db(db_path: Path) -> list[tuple[int, int]]:
    conn = sqlite3.connect(db_path)
    try:
        schema = SCHEMA_SQL.read_text(encoding="utf-8")
        conn.executescript(schema)
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("Кав'ярні",))

        created_at = _now_iso()
        pairs: list[tuple[int, int]] = []
        for idx in range(1, 7):
            conn.execute(
                """
                INSERT INTO places(
                    service_id, name, description, address, keywords,
                    is_published, is_verified, verified_tier, verified_until, business_enabled
                ) VALUES(?, ?, ?, ?, ?, 1, 0, NULL, NULL, 0)
                """,
                (1, f"Parity Place {idx}", "Desc", "Addr", f"parity {idx}"),
            )
            place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            tg_user_id = 41000 + idx
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


async def _state_snapshot(repo, place_id: int) -> tuple[str, str, int, str | None, str | None, str | None, str | None]:
    sub = await repo.ensure_subscription(int(place_id))
    place = await repo.get_place(int(place_id))
    return (
        str(sub.get("tier") or ""),
        str(sub.get("status") or ""),
        int(place.get("is_verified") or 0),
        str(place.get("verified_tier") or "") or None,
        str(place.get("verified_until") or "") or None,
        str(sub.get("starts_at") or "") or None,
        str(sub.get("expires_at") or "") or None,
    )


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
    from config import CFG  # noqa: WPS433

    repo = BusinessRepository()
    service = BusinessCabinetService(repository=repo)

    # Case map:
    # 1 - mock cancel
    # 2 - telegram cancel
    # 3 - mock fail
    # 4 - telegram fail
    # 5 - mock success
    # 6 - telegram success
    (mock_cancel_place, mock_cancel_user), (tg_cancel_place, tg_cancel_user), (mock_fail_place, mock_fail_user), (
        tg_fail_place,
        tg_fail_user,
    ), (mock_success_place, mock_success_user), (tg_success_place, tg_success_user) = pairs

    # mock cancel
    CFG.business_payment_provider = "mock"
    intent = await service.create_payment_intent(
        tg_user_id=int(mock_cancel_user),
        place_id=int(mock_cancel_place),
        tier="light",
        source="plans",
    )
    ext = str(intent.get("external_payment_id") or "")
    out = await service.apply_mock_payment_result(
        tg_user_id=int(mock_cancel_user),
        place_id=int(mock_cancel_place),
        tier="light",
        external_payment_id=ext,
        result="cancel",
    )
    _assert(bool(out.get("applied")), f"mock cancel not applied: {out}")
    dup = await service.apply_mock_payment_result(
        tg_user_id=int(mock_cancel_user),
        place_id=int(mock_cancel_place),
        tier="light",
        external_payment_id=ext,
        result="cancel",
    )
    _assert(not bool(dup.get("applied")) and bool(dup.get("duplicate")), f"mock cancel duplicate broken: {dup}")
    mock_cancel_state = await _state_snapshot(repo, mock_cancel_place)

    # telegram cancel
    CFG.business_payment_provider = "telegram_stars"
    intent = await service.create_payment_intent(
        tg_user_id=int(tg_cancel_user),
        place_id=int(tg_cancel_place),
        tier="light",
        source="plans",
    )
    invoice_payload = str(intent.get("invoice_payload") or "")
    cancel_id = f"tg_cancel_parity_{int(time.time())}"
    out = await service.apply_telegram_stars_terminal_event(
        tg_user_id=int(tg_cancel_user),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        terminal_kind="cancel",
        telegram_payment_charge_id=cancel_id,
        provider_payment_charge_id="provider-cancel-parity",
        raw_payload_json=None,
        reason="parity",
    )
    _assert(bool(out.get("applied")), f"telegram cancel not applied: {out}")
    dup = await service.apply_telegram_stars_terminal_event(
        tg_user_id=int(tg_cancel_user),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        terminal_kind="canceled",
        telegram_payment_charge_id=cancel_id,
        provider_payment_charge_id="provider-cancel-parity",
        raw_payload_json=None,
        reason="parity-dup",
    )
    _assert(not bool(dup.get("applied")) and bool(dup.get("duplicate")), f"telegram cancel duplicate broken: {dup}")
    tg_cancel_state = await _state_snapshot(repo, tg_cancel_place)

    # mock fail
    CFG.business_payment_provider = "mock"
    intent = await service.create_payment_intent(
        tg_user_id=int(mock_fail_user),
        place_id=int(mock_fail_place),
        tier="light",
        source="plans",
    )
    ext = str(intent.get("external_payment_id") or "")
    out = await service.apply_mock_payment_result(
        tg_user_id=int(mock_fail_user),
        place_id=int(mock_fail_place),
        tier="light",
        external_payment_id=ext,
        result="fail",
    )
    _assert(bool(out.get("applied")), f"mock fail not applied: {out}")
    dup = await service.apply_mock_payment_result(
        tg_user_id=int(mock_fail_user),
        place_id=int(mock_fail_place),
        tier="light",
        external_payment_id=ext,
        result="fail",
    )
    _assert(not bool(dup.get("applied")) and bool(dup.get("duplicate")), f"mock fail duplicate broken: {dup}")
    mock_fail_state = await _state_snapshot(repo, mock_fail_place)

    # telegram fail
    CFG.business_payment_provider = "telegram_stars"
    intent = await service.create_payment_intent(
        tg_user_id=int(tg_fail_user),
        place_id=int(tg_fail_place),
        tier="light",
        source="plans",
    )
    invoice_payload = str(intent.get("invoice_payload") or "")
    fail_id = f"tg_fail_parity_{int(time.time())}"
    out = await service.apply_telegram_stars_terminal_event(
        tg_user_id=int(tg_fail_user),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        terminal_kind="fail",
        telegram_payment_charge_id=fail_id,
        provider_payment_charge_id="provider-fail-parity",
        raw_payload_json=None,
        reason="parity",
    )
    _assert(bool(out.get("applied")), f"telegram fail not applied: {out}")
    dup = await service.apply_telegram_stars_terminal_event(
        tg_user_id=int(tg_fail_user),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        terminal_kind="failed",
        telegram_payment_charge_id=fail_id,
        provider_payment_charge_id="provider-fail-parity",
        raw_payload_json=None,
        reason="parity-dup",
    )
    _assert(not bool(dup.get("applied")) and bool(dup.get("duplicate")), f"telegram fail duplicate broken: {dup}")
    tg_fail_state = await _state_snapshot(repo, tg_fail_place)

    # mock success
    CFG.business_payment_provider = "mock"
    intent = await service.create_payment_intent(
        tg_user_id=int(mock_success_user),
        place_id=int(mock_success_place),
        tier="light",
        source="plans",
    )
    ext = str(intent.get("external_payment_id") or "")
    out = await service.apply_mock_payment_result(
        tg_user_id=int(mock_success_user),
        place_id=int(mock_success_place),
        tier="light",
        external_payment_id=ext,
        result="success",
    )
    _assert(bool(out.get("applied")), f"mock success not applied: {out}")
    dup = await service.apply_mock_payment_result(
        tg_user_id=int(mock_success_user),
        place_id=int(mock_success_place),
        tier="light",
        external_payment_id=ext,
        result="success",
    )
    _assert(not bool(dup.get("applied")) and bool(dup.get("duplicate")), f"mock success duplicate broken: {dup}")
    mock_success_state = await _state_snapshot(repo, mock_success_place)

    # telegram success
    CFG.business_payment_provider = "telegram_stars"
    intent = await service.create_payment_intent(
        tg_user_id=int(tg_success_user),
        place_id=int(tg_success_place),
        tier="light",
        source="plans",
    )
    invoice_payload = str(intent.get("invoice_payload") or "")
    pre = await service.validate_telegram_stars_pre_checkout(
        tg_user_id=int(tg_success_user),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        pre_checkout_query_id="smoke-parity-success",
    )
    _assert(int(pre.get("place_id") or 0) == int(tg_success_place), f"pre_checkout place mismatch: {pre}")
    success_id = f"tg_success_parity_{int(time.time())}"
    out = await service.apply_telegram_stars_successful_payment(
        tg_user_id=int(tg_success_user),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        subscription_expiration_date=None,
        is_recurring=True,
        is_first_recurring=True,
        telegram_payment_charge_id=success_id,
        provider_payment_charge_id="provider-success-parity",
        raw_payload_json=None,
    )
    _assert(bool(out.get("applied")), f"telegram success not applied: {out}")
    dup = await service.apply_telegram_stars_successful_payment(
        tg_user_id=int(tg_success_user),
        invoice_payload=invoice_payload,
        total_amount=1000,
        currency="XTR",
        subscription_expiration_date=None,
        is_recurring=True,
        is_first_recurring=False,
        telegram_payment_charge_id=success_id,
        provider_payment_charge_id="provider-success-parity",
        raw_payload_json=None,
    )
    _assert(not bool(dup.get("applied")) and bool(dup.get("duplicate")), f"telegram success duplicate broken: {dup}")
    tg_success_state = await _state_snapshot(repo, tg_success_place)

    # Provider parity assertions (non-success outcomes must match state contract).
    _assert(mock_cancel_state == tg_cancel_state, f"cancel parity mismatch: {mock_cancel_state} != {tg_cancel_state}")
    _assert(mock_fail_state == tg_fail_state, f"fail parity mismatch: {mock_fail_state} != {tg_fail_state}")
    expected_state = ("free", "inactive", 0, None, None, None, None)
    _assert(mock_cancel_state == expected_state, f"unexpected non-success state (cancel): {mock_cancel_state}")
    _assert(mock_fail_state == expected_state, f"unexpected non-success state (fail): {mock_fail_state}")

    # Success parity assertions.
    _assert(mock_success_state[:4] == tg_success_state[:4], f"success parity mismatch: {mock_success_state} != {tg_success_state}")
    success_expected = ("light", "active", 1, "light")
    _assert(mock_success_state[:4] == success_expected, f"unexpected success state (mock): {mock_success_state}")
    _assert(tg_success_state[:4] == success_expected, f"unexpected success state (tg): {tg_success_state}")
    _assert(bool(mock_success_state[4]), f"mock success verified_until missing: {mock_success_state}")
    _assert(bool(tg_success_state[4]), f"tg success verified_until missing: {tg_success_state}")
    _assert(bool(mock_success_state[5]) and bool(mock_success_state[6]), f"mock success starts/expires missing: {mock_success_state}")
    _assert(bool(tg_success_state[5]) and bool(tg_success_state[6]), f"tg success starts/expires missing: {tg_success_state}")

    import aiosqlite  # noqa: WPS433

    async with aiosqlite.connect(os.environ["DB_PATH"]) as db:
        async with db.execute(
            """
            SELECT provider, event_type, COUNT(*)
              FROM business_payment_events
             WHERE event_type IN ('invoice_created', 'pre_checkout_ok', 'payment_succeeded', 'payment_canceled', 'payment_failed')
             GROUP BY provider, event_type
             ORDER BY provider, event_type
            """
        ) as cur:
            rows = await cur.fetchall()
    counts = {(str(r[0]), str(r[1])): int(r[2]) for r in rows}
    _assert(counts.get(("mock", "payment_canceled"), 0) >= 1, f"missing mock canceled event: {counts}")
    _assert(counts.get(("telegram_stars", "payment_canceled"), 0) >= 1, f"missing tg canceled event: {counts}")
    _assert(counts.get(("mock", "payment_failed"), 0) >= 1, f"missing mock failed event: {counts}")
    _assert(counts.get(("telegram_stars", "payment_failed"), 0) >= 1, f"missing tg failed event: {counts}")
    _assert(counts.get(("mock", "invoice_created"), 0) >= 1, f"missing mock invoice_created event: {counts}")
    _assert(counts.get(("telegram_stars", "invoice_created"), 0) >= 1, f"missing tg invoice_created event: {counts}")
    _assert(counts.get(("telegram_stars", "pre_checkout_ok"), 0) >= 1, f"missing tg pre_checkout_ok event: {counts}")
    _assert(counts.get(("mock", "payment_succeeded"), 0) >= 1, f"missing mock payment_succeeded event: {counts}")
    _assert(counts.get(("telegram_stars", "payment_succeeded"), 0) >= 1, f"missing tg payment_succeeded event: {counts}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-provider-parity-"))
    try:
        db_path = tmpdir / "state.db"
        pairs = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "1")
        os.environ["BUSINESS_MODE"] = "1"
        os.environ["BUSINESS_PAYMENT_PROVIDER"] = "mock"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks(pairs))
        print("OK: business payment provider parity smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
