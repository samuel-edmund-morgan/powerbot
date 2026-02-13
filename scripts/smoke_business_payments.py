#!/usr/bin/env python3
"""
Smoke test for business mock payments:
- success activates subscription/verified flags for light/pro/partner
- cancel/fail do not activate subscription
- repeated success for same external_payment_id is idempotent

Run:
  python3 scripts/smoke_business_payments.py
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
    candidates.extend(
        [
            Path.cwd(),
            Path("/app"),
            Path("/workspace"),
        ]
    )
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

        created_at = _now_iso()
        # 9 places:
        # 1..3 => success tiers light/pro/partner
        # 4..6 => cancel tiers light/pro/partner
        # 7..9 => fail tiers light/pro/partner
        for idx in range(1, 10):
            conn.execute(
                """
                INSERT INTO places(
                    service_id, name, description, address, keywords,
                    is_published,
                    is_verified, verified_tier, verified_until, business_enabled
                ) VALUES(?, ?, ?, ?, ?, 1, 0, NULL, NULL, 0)
                """,
                (
                    1,
                    f"Place {idx}",
                    "Desc",
                    "Addr",
                    f"place {idx}",
                ),
            )
            place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            tg_user_id = 10000 + idx
            conn.execute(
                """
                INSERT INTO business_owners(
                    place_id, tg_user_id, role, status, created_at, approved_at, approved_by
                ) VALUES(?, ?, 'owner', 'approved', ?, ?, ?)
                """,
                (place_id, tg_user_id, created_at, created_at, 1),
            )
        conn.commit()
    finally:
        conn.close()


async def _run_checks() -> None:
    # Local smoke environment may not have python-dotenv installed.
    # Config only needs load_dotenv symbol, so provide a no-op fallback.
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    # Import only after env is set, because config reads env on import.
    from business.repository import BusinessRepository  # noqa: WPS433
    from business.service import BusinessCabinetService  # noqa: WPS433

    repo = BusinessRepository()
    service = BusinessCabinetService(repository=repo)

    tiers = ["light", "pro", "partner"]
    success_cases = [(idx + 1, 10001 + idx, tier) for idx, tier in enumerate(tiers)]
    for place_id, tg_user_id, tier in success_cases:
        intent = await service.create_mock_payment_intent(
            tg_user_id=tg_user_id,
            place_id=place_id,
            tier=tier,
            source="plans",
        )
        ext = str(intent["external_payment_id"])
        outcome = await service.apply_mock_payment_result(
            tg_user_id=tg_user_id,
            place_id=place_id,
            tier=tier,
            external_payment_id=ext,
            result="success",
        )
        _assert(bool(outcome.get("applied")), f"success not applied for {tier}")
        _assert(not bool(outcome.get("duplicate")), f"success wrongly duplicate for {tier}")

        duplicate = await service.apply_mock_payment_result(
            tg_user_id=tg_user_id,
            place_id=place_id,
            tier=tier,
            external_payment_id=ext,
            result="success",
        )
        _assert(not bool(duplicate.get("applied")), f"duplicate applied for {tier}")
        _assert(bool(duplicate.get("duplicate")), f"duplicate flag missing for {tier}")

        sub = await repo.ensure_subscription(place_id)
        _assert(str(sub.get("tier")) == tier, f"tier mismatch for {tier}: {sub}")
        _assert(str(sub.get("status")) == "active", f"status mismatch for {tier}: {sub}")
        _assert(bool(sub.get("expires_at")), f"expires_at missing for {tier}: {sub}")

        place = await repo.get_place(place_id)
        _assert(int(place.get("is_verified") or 0) == 1, f"is_verified mismatch for {tier}: {place}")
        _assert(str(place.get("verified_tier")) == tier, f"verified_tier mismatch for {tier}: {place}")
        _assert(bool(place.get("verified_until")), f"verified_until missing for {tier}: {place}")

    # Cancel/fail should not activate for each tier.
    cancel_cases = [(idx + 4, 10004 + idx, tier) for idx, tier in enumerate(tiers)]
    for place_id, tg_user_id, tier in cancel_cases:
        cancel_intent = await service.create_mock_payment_intent(
            tg_user_id=tg_user_id,
            place_id=place_id,
            tier=tier,
            source="plans",
        )
        ext = str(cancel_intent["external_payment_id"])
        cancel_outcome = await service.apply_mock_payment_result(
            tg_user_id=tg_user_id,
            place_id=place_id,
            tier=tier,
            external_payment_id=ext,
            result="cancel",
        )
        _assert(bool(cancel_outcome.get("applied")), f"cancel event not recorded for {tier}")
        cancel_duplicate = await service.apply_mock_payment_result(
            tg_user_id=tg_user_id,
            place_id=place_id,
            tier=tier,
            external_payment_id=ext,
            result="cancel",
        )
        _assert(not bool(cancel_duplicate.get("applied")), f"cancel duplicate applied for {tier}")
        _assert(bool(cancel_duplicate.get("duplicate")), f"cancel duplicate flag missing for {tier}")

        cancel_sub = await repo.ensure_subscription(place_id)
        _assert(str(cancel_sub.get("tier")) == "free", f"cancel changed tier unexpectedly for {tier}: {cancel_sub}")
        _assert(str(cancel_sub.get("status")) == "inactive", f"cancel changed status unexpectedly for {tier}: {cancel_sub}")
        cancel_place = await repo.get_place(place_id)
        _assert(int(cancel_place.get("is_verified") or 0) == 0, f"cancel set verified unexpectedly for {tier}: {cancel_place}")

    fail_cases = [(idx + 7, 10007 + idx, tier) for idx, tier in enumerate(tiers)]
    for place_id, tg_user_id, tier in fail_cases:
        fail_intent = await service.create_mock_payment_intent(
            tg_user_id=tg_user_id,
            place_id=place_id,
            tier=tier,
            source="plans",
        )
        ext = str(fail_intent["external_payment_id"])
        fail_outcome = await service.apply_mock_payment_result(
            tg_user_id=tg_user_id,
            place_id=place_id,
            tier=tier,
            external_payment_id=ext,
            result="fail",
        )
        _assert(bool(fail_outcome.get("applied")), f"fail event not recorded for {tier}")
        fail_duplicate = await service.apply_mock_payment_result(
            tg_user_id=tg_user_id,
            place_id=place_id,
            tier=tier,
            external_payment_id=ext,
            result="fail",
        )
        _assert(not bool(fail_duplicate.get("applied")), f"fail duplicate applied for {tier}")
        _assert(bool(fail_duplicate.get("duplicate")), f"fail duplicate flag missing for {tier}")

        fail_sub = await repo.ensure_subscription(place_id)
        _assert(str(fail_sub.get("tier")) == "free", f"fail changed tier unexpectedly for {tier}: {fail_sub}")
        _assert(str(fail_sub.get("status")) == "inactive", f"fail changed status unexpectedly for {tier}: {fail_sub}")
        fail_place = await repo.get_place(place_id)
        _assert(int(fail_place.get("is_verified") or 0) == 0, f"fail set verified unexpectedly for {tier}: {fail_place}")

    # Sanity: we should have exactly one payment_succeeded per successful intent.
    import aiosqlite  # noqa: WPS433

    async with aiosqlite.connect(os.environ["DB_PATH"]) as db:
        async with db.execute(
            """
            SELECT provider, external_payment_id, event_type, COUNT(*) AS cnt
              FROM business_payment_events
             WHERE event_type = 'payment_succeeded'
             GROUP BY provider, external_payment_id, event_type
            """
        ) as cur:
            rows = await cur.fetchall()
    _assert(len(rows) == 3, f"unexpected payment_succeeded groups: {rows}")
    for row in rows:
        _assert(int(row[3]) == 1, f"non-idempotent succeeded rows: {rows}")

    async with aiosqlite.connect(os.environ["DB_PATH"]) as db:
        async with db.execute(
            """
            SELECT event_type, COUNT(*)
              FROM business_payment_events
             WHERE event_type IN ('payment_canceled', 'payment_failed')
             GROUP BY event_type
            """
        ) as cur:
            non_success_rows = await cur.fetchall()
    non_success_counts = {str(row[0]): int(row[1]) for row in non_success_rows}
    _assert(int(non_success_counts.get("payment_canceled", 0)) == 3, f"unexpected payment_canceled count: {non_success_counts}")
    _assert(int(non_success_counts.get("payment_failed", 0)) == 3, f"unexpected payment_failed count: {non_success_counts}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-business-payments-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        # Minimal env required by src/config.py and business services.
        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "1")
        os.environ["BUSINESS_PAYMENT_PROVIDER"] = "mock"
        os.environ["BUSINESS_MODE"] = "1"

        # Make project importable.
        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: business mock payments smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
