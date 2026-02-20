#!/usr/bin/env python3
"""
Smoke test: promo-code open contract (resident CTA + analytics).

Validates:
- Light verified place with promo_code gets resident CTA `pcoupon_<place_id>`.
- `coupon_open` is persisted in `place_clicks_daily`.
- Owner business card stats show "Відкриття промокоду" with aggregated value.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
import time
import types
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
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_coupon_contract__",))
        conn.commit()
    finally:
        conn.close()


def _callbacks(keyboard) -> list[str]:
    result: list[str] = []
    for row in keyboard.inline_keyboard:
        for btn in row:
            value = getattr(btn, "callback_data", None)
            if value:
                result.append(str(value))
    return result


async def _run_checks() -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    from business import get_business_service  # noqa: WPS433
    from business.handlers import build_business_card_text  # noqa: WPS433
    from business.repository import BusinessRepository  # noqa: WPS433
    from business.service import BusinessCabinetService  # noqa: WPS433
    from database import get_place, record_place_click  # noqa: WPS433
    from handlers import build_place_detail_keyboard  # noqa: WPS433

    repo = BusinessRepository()
    service = BusinessCabinetService(repository=repo)

    admin_id = 800001
    service.admin_ids.add(admin_id)

    services = await repo.list_services()
    _assert(bool(services), "no services available in temp DB")
    service_id = int(services[0].get("id") or 0)
    _assert(service_id > 0, f"invalid service id: {services[0]}")

    stamp = int(time.time())
    owner_tg_user_id = 810000 + (stamp % 10000)

    created = await service.register_new_business(
        tg_user_id=owner_tg_user_id,
        service_id=service_id,
        place_name=f"Coupon Contract {stamp}",
        description="promo smoke",
        address="addr",
    )
    owner = created.get("owner") or {}
    place = created.get("place") or {}
    owner_id = int(owner.get("id") or 0)
    place_id = int(place.get("id") or 0)
    _assert(owner_id > 0 and place_id > 0, f"invalid created objects: {created}")

    await service.approve_owner_request(admin_id, owner_id)
    await service.change_subscription_tier(owner_tg_user_id, place_id, "light")
    await service.update_place_business_profile_field(
        owner_tg_user_id,
        place_id,
        "promo_code",
        "NABUTLER10",
    )

    place_row = await get_place(place_id)
    _assert(place_row is not None, "place must be available after approve")
    enriched = (await get_business_service().enrich_places_for_main_bot([place_row]))[0]
    _assert(bool(enriched.get("is_verified")), f"expected verified place in business mode: {enriched}")
    _assert(str(enriched.get("promo_code") or "") == "NABUTLER10", f"promo code mismatch: {enriched}")

    keyboard = build_place_detail_keyboard(
        enriched,
        likes_count=0,
        user_liked=False,
        business_enabled=True,
    )
    callbacks = _callbacks(keyboard)
    _assert(
        any(cb == f"pcoupon_{place_id}" for cb in callbacks),
        f"resident card must contain coupon CTA for verified promo place: callbacks={callbacks}",
    )

    for _ in range(3):
        await record_place_click(place_id, "coupon_open")

    clicks_30d = await repo.get_place_clicks_sum(place_id, action="coupon_open", days=30)
    _assert(int(clicks_30d) == 3, f"coupon_open aggregate mismatch: {clicks_30d}")

    owner_rows = await service.list_user_businesses(owner_tg_user_id)
    owner_item = next((row for row in owner_rows if int(row.get("place_id") or 0) == place_id), None)
    _assert(owner_item is not None, f"owner business row not found for place_id={place_id}")

    card_text = await build_business_card_text(owner_item, days=30)
    _assert(
        "Відкриття промокоду: <b>3</b>" in card_text,
        f"owner analytics must include coupon opens count: {card_text}",
    )


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-coupon-open-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "800001")
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: business coupon-open contract smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
