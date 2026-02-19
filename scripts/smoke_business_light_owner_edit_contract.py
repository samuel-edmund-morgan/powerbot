#!/usr/bin/env python3
"""
Smoke test: owner edit contract for Free vs Light tiers.

Checks:
- Free owner cannot edit place profile fields.
- After Light activation owner can edit:
  name/description/address + opening_hours/link/contact.
- Premium-only fields stay blocked on Light.
- Verified flags are synced for active Light subscription.
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
            ) VALUES(1, 'Light Contract Place', 'Desc', 'Addr', 'contract', 1, 0, NULL, NULL, 1)
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


async def _run_checks(place_id: int) -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    from business.repository import BusinessRepository  # noqa: WPS433
    from business.service import AccessDeniedError, BusinessCabinetService  # noqa: WPS433

    repo = BusinessRepository()
    service = BusinessCabinetService(repository=repo)

    # Free owner must not edit.
    try:
        await service.update_place_field(OWNER_ID, int(place_id), "description", "Blocked on free")
        raise AssertionError("Expected AccessDeniedError for free basic edit")
    except AccessDeniedError:
        pass

    try:
        await service.update_place_business_profile_field(OWNER_ID, int(place_id), "opening_hours", "09:00-21:00")
        raise AssertionError("Expected AccessDeniedError for free business-profile edit")
    except AccessDeniedError:
        pass

    try:
        await service.update_place_contact(
            OWNER_ID,
            place_id=int(place_id),
            contact_type="chat",
            contact_value="@free_forbidden",
        )
        raise AssertionError("Expected AccessDeniedError for free contact edit")
    except AccessDeniedError:
        pass

    # Activate Light.
    paid = await service.change_subscription_tier(OWNER_ID, int(place_id), "light")
    _assert(str(paid.get("tier") or "") == "light", f"light tier mismatch: {paid}")
    _assert(str(paid.get("status") or "") == "active", f"light status mismatch: {paid}")
    _assert(bool(paid.get("expires_at")), f"light expires_at missing: {paid}")

    # Core profile edits.
    await service.update_place_field(OWNER_ID, int(place_id), "name", "Light Contract Place Updated")
    await service.update_place_field(OWNER_ID, int(place_id), "description", "Updated description")
    await service.update_place_field(OWNER_ID, int(place_id), "address", "Newcastle (24-в), секція 2")

    # Light-level business profile edits.
    await service.update_place_business_profile_field(OWNER_ID, int(place_id), "opening_hours", "09:00-21:00")
    await service.update_place_business_profile_field(
        OWNER_ID,
        int(place_id),
        "link_url",
        "https://example.org/light-contract",
    )
    await service.update_place_contact(
        OWNER_ID,
        place_id=int(place_id),
        contact_type="chat",
        contact_value="@light_contract_chat",
    )

    # Premium-only fields must stay blocked on Light.
    try:
        await service.update_place_business_profile_field(
            OWNER_ID,
            int(place_id),
            "menu_url",
            "https://example.org/menu",
        )
        raise AssertionError("Expected AccessDeniedError for Premium-only field on Light")
    except AccessDeniedError:
        pass

    place = await repo.get_place(int(place_id))
    _assert(str(place.get("name") or "") == "Light Contract Place Updated", f"name mismatch: {place}")
    _assert(str(place.get("description") or "") == "Updated description", f"description mismatch: {place}")
    _assert(str(place.get("address") or "") == "Newcastle (24-в), секція 2", f"address mismatch: {place}")
    _assert(str(place.get("opening_hours") or "") == "09:00-21:00", f"opening_hours mismatch: {place}")
    _assert(str(place.get("link_url") or "") == "https://example.org/light-contract", f"link_url mismatch: {place}")
    _assert(str(place.get("contact_type") or "") == "chat", f"contact_type mismatch: {place}")
    _assert(str(place.get("contact_value") or "") == "@light_contract_chat", f"contact_value mismatch: {place}")
    _assert(int(place.get("is_verified") or 0) == 1, f"verified flag mismatch: {place}")
    _assert(str(place.get("verified_tier") or "") == "light", f"verified_tier mismatch: {place}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-biz-light-edit-"))
    try:
        db_path = tmpdir / "state.db"
        place_id = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ["ADMIN_IDS"] = str(ADMIN_ID)
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks(place_id))
        print("OK: business light owner edit contract smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
