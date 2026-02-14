#!/usr/bin/env python3
"""
Smoke test: admin promo/subscription tier operations.

Validates:
- admin_set_subscription_tier applies free/light/pro/partner correctly
- verified flags are synchronized with tier/status
- months input is clamped to 1..12 range
- audit log records admin_subscription_set actions

Run:
  python3 scripts/smoke_business_admin_subscription_promo.py
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


def _setup_temp_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def _parse_iso(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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

    admin_id = 1
    service.admin_ids.add(admin_id)

    service_id = await repo.get_or_create_service_id("__smoke_admin_subscription_promo__")
    place_id = await repo.create_place(
        service_id=service_id,
        name="Promo Place",
        description="Desc",
        address="Ньюкасл (24-в)",
    )
    await repo.ensure_subscription(place_id)

    async def _assert_place_flags(*, is_verified: int, verified_tier: str | None) -> None:
        place = await repo.get_place(place_id)
        _assert(place is not None, "place must exist")
        _assert(int(place.get("is_verified") or 0) == int(is_verified), f"is_verified mismatch: {place}")
        vt = (place.get("verified_tier") or None)
        _assert(vt == verified_tier, f"verified_tier mismatch: {place}")

    # Free tier.
    sub = await service.admin_set_subscription_tier(admin_id, place_id=place_id, tier="free", months=3)
    _assert(str(sub.get("tier") or "") == "free", f"free tier mismatch: {sub}")
    _assert(str(sub.get("status") or "") == "inactive", f"free status mismatch: {sub}")
    _assert(not sub.get("starts_at") and not sub.get("expires_at"), f"free dates must be null: {sub}")
    await _assert_place_flags(is_verified=0, verified_tier=None)

    # Light tier (1 month).
    sub = await service.admin_set_subscription_tier(admin_id, place_id=place_id, tier="light", months=1)
    _assert(str(sub.get("tier") or "") == "light", f"light tier mismatch: {sub}")
    _assert(str(sub.get("status") or "") == "active", f"light status mismatch: {sub}")
    starts = _parse_iso(sub.get("starts_at"))
    expires = _parse_iso(sub.get("expires_at"))
    _assert(starts is not None and expires is not None and expires > starts, f"light dates invalid: {sub}")
    await _assert_place_flags(is_verified=1, verified_tier="light")

    # Pro tier (2 months).
    sub = await service.admin_set_subscription_tier(admin_id, place_id=place_id, tier="pro", months=2)
    _assert(str(sub.get("tier") or "") == "pro", f"pro tier mismatch: {sub}")
    _assert(str(sub.get("status") or "") == "active", f"pro status mismatch: {sub}")
    await _assert_place_flags(is_verified=1, verified_tier="pro")

    # Partner tier with too large months should clamp to 12.
    before = datetime.now(timezone.utc)
    sub = await service.admin_set_subscription_tier(admin_id, place_id=place_id, tier="partner", months=99)
    _assert(str(sub.get("tier") or "") == "partner", f"partner tier mismatch: {sub}")
    _assert(str(sub.get("status") or "") == "active", f"partner status mismatch: {sub}")
    partner_expires = _parse_iso(sub.get("expires_at"))
    _assert(partner_expires is not None, "partner expires_at missing")
    # 12 months ~ 360 days in current implementation.
    delta_days = (partner_expires - before).days
    _assert(330 <= delta_days <= 370, f"months clamp seems broken (delta_days={delta_days}): {sub}")
    await _assert_place_flags(is_verified=1, verified_tier="partner")

    import aiosqlite  # noqa: WPS433

    async with aiosqlite.connect(os.environ["DB_PATH"]) as db:
        async with db.execute(
            """
            SELECT action, COUNT(*)
              FROM business_audit_log
             WHERE place_id = 1
             GROUP BY action
            """
        ) as cur:
            rows = await cur.fetchall()

    counts = {str(action): int(count) for action, count in rows}
    _assert(counts.get("admin_subscription_set", 0) == 4, f"audit count mismatch: {counts}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-admin-subscription-promo-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "1")
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: business admin subscription promo smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
