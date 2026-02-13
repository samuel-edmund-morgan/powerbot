#!/usr/bin/env python3
"""
Smoke test for admin business payments paging/export contract.

What it validates:
- `list_payment_events_admin()` returns stable paged slices with correct total.
- pages do not overlap and cover all rows when iterated.
- owner/contact fields are present (for admin communication needs).
- export-like loop (page_size=100) collects full dataset.

Run:
  python3 scripts/smoke_admin_business_payments_paging.py
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

TOTAL_EVENTS = 137


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

        created_at = _now_iso()
        place_ids: list[int] = []
        for idx in range(1, 13):
            conn.execute(
                """
                INSERT INTO places(
                    service_id, name, description, address, keywords,
                    is_published, is_verified, verified_tier, verified_until, business_enabled
                ) VALUES(?, ?, ?, ?, ?, 1, 0, NULL, NULL, 1)
                """,
                (1, f"Place {idx:02d}", "Desc", f"Addr {idx}", f"place {idx}"),
            )
            place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            place_ids.append(place_id)
            tg_user_id = 30000 + idx
            conn.execute(
                """
                INSERT INTO business_owners(
                    place_id, tg_user_id, role, status, created_at, approved_at, approved_by
                ) VALUES(?, ?, 'owner', 'approved', ?, ?, ?)
                """,
                (place_id, tg_user_id, created_at, created_at, 1),
            )

        event_types = [
            "invoice_created",
            "pre_checkout_ok",
            "payment_succeeded",
            "payment_failed",
            "payment_canceled",
            "refund",
        ]
        for idx in range(1, TOTAL_EVENTS + 1):
            place_id = place_ids[(idx - 1) % len(place_ids)]
            provider = "telegram_stars" if idx % 2 == 0 else "mock"
            event_type = event_types[(idx - 1) % len(event_types)]
            external_payment_id = f"evt_{idx:04d}_{provider}_{event_type}"
            amount_stars = 1000 if event_type not in {"invoice_created", "pre_checkout_ok"} else 1000
            status = "processed"
            if event_type == "payment_failed":
                status = "failed"
            elif event_type == "payment_canceled":
                status = "canceled"
            elif event_type == "invoice_created":
                status = "new"
            conn.execute(
                """
                INSERT INTO business_payment_events(
                    place_id, provider, external_payment_id, event_type,
                    amount_stars, currency, status, raw_payload_json, created_at, processed_at
                ) VALUES(?, ?, ?, ?, ?, 'XTR', ?, '{}', ?, ?)
                """,
                (
                    place_id,
                    provider,
                    external_payment_id,
                    event_type,
                    amount_stars,
                    status,
                    created_at,
                    created_at,
                ),
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

    from business.service import BusinessCabinetService  # noqa: WPS433

    service = BusinessCabinetService()
    admin_id = 1

    page0_rows, total = await service.list_payment_events_admin(admin_id, limit=10, offset=0)
    page1_rows, total_again = await service.list_payment_events_admin(admin_id, limit=10, offset=10)

    _assert(int(total) == TOTAL_EVENTS, f"unexpected total: {total}")
    _assert(int(total_again) == TOTAL_EVENTS, f"unexpected total on second page: {total_again}")
    _assert(len(page0_rows) == 10, f"unexpected page0 size: {len(page0_rows)}")
    _assert(len(page1_rows) == 10, f"unexpected page1 size: {len(page1_rows)}")

    page0_ids = {int(row.get("id") or 0) for row in page0_rows}
    page1_ids = {int(row.get("id") or 0) for row in page1_rows}
    _assert(page0_ids.isdisjoint(page1_ids), "paging overlap detected between first pages")

    for row in page0_rows + page1_rows:
        _assert("owner_tg_user_id" in row, f"owner_tg_user_id missing in row: {row}")
        _assert("owner_status" in row, f"owner_status missing in row: {row}")
        _assert("place_name" in row, f"place_name missing in row: {row}")
        _assert("external_payment_id" in row, f"external_payment_id missing in row: {row}")

    all_rows: list[dict] = []
    offset = 0
    page_size = 100
    while True:
        rows, loop_total = await service.list_payment_events_admin(admin_id, limit=page_size, offset=offset)
        all_rows.extend(rows)
        offset += len(rows)
        if not rows or offset >= int(loop_total):
            break

    _assert(len(all_rows) == TOTAL_EVENTS, f"export loop count mismatch: {len(all_rows)} != {TOTAL_EVENTS}")
    all_ids = [int(row.get("id") or 0) for row in all_rows]
    _assert(len(set(all_ids)) == TOTAL_EVENTS, "duplicate/missing event ids in export loop")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-admin-payments-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ["ADMIN_IDS"] = "1"
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: admin business payments paging smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
