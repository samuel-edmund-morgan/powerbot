#!/usr/bin/env python3
"""
Smoke-check: resident catalog behavior for business mode OFF vs ON service path.

Flow:
1) create draft place + owner request
2) approve owner
3) apply mock successful payment (light)
4) check resident catalog row:
   - OFF path (NoopBusinessService): no business metadata keys added
   - ON path (BusinessIntegrationService): verified/business metadata is present

Run:
  python3 scripts/smoke_business_mode_catalog_compare.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path


def _setup_import_path() -> None:
    for candidate in (
        Path.cwd() / "src",
        Path("/app/src"),
    ):
        if candidate.exists():
            sys.path.insert(0, str(candidate))
            return


_setup_import_path()

from business.repository import BusinessRepository  # noqa: E402
from business.service import (  # noqa: E402
    BusinessCabinetService,
    BusinessIntegrationService,
    NoopBusinessService,
)
from database import get_places_by_service_with_likes  # noqa: E402


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


async def _pick_service_id(repository: BusinessRepository) -> int:
    services = await repository.list_services()
    if services:
        return int(services[0]["id"])
    return await repository.get_or_create_service_id("__smoke_mode_catalog_compare__")


async def main() -> None:
    repository = BusinessRepository()
    cabinet = BusinessCabinetService(repository=repository)
    integration = BusinessIntegrationService(repository=repository)
    noop = NoopBusinessService()

    admin_id = next(iter(cabinet.admin_ids), None) or 1
    cabinet.admin_ids.add(int(admin_id))

    stamp = int(time.time())
    owner_tg_user_id = int(f"97{stamp % 100000000:08d}")
    service_id = await _pick_service_id(repository)

    created = await cabinet.register_new_business(
        tg_user_id=owner_tg_user_id,
        service_id=int(service_id),
        place_name=f"Smoke Mode Compare Place {stamp}",
        description="Temporary place for mode compare smoke test",
        address="Smoke address",
    )

    owner = created.get("owner") or {}
    place = created.get("place") or {}
    owner_id = int(owner.get("id") or 0)
    place_id = int(place.get("id") or 0)
    if owner_id <= 0 or place_id <= 0:
        raise AssertionError(f"invalid created objects: owner={owner}, place={place}")

    try:
        await cabinet.approve_owner_request(int(admin_id), int(owner_id))
        intent = await cabinet.create_mock_payment_intent(
            tg_user_id=int(owner_tg_user_id),
            place_id=int(place_id),
            tier="light",
            source="plans",
        )
        ext_payment_id = str(intent.get("external_payment_id") or "")
        _assert(ext_payment_id != "", "missing external_payment_id for mock payment intent")
        applied = await cabinet.apply_mock_payment_result(
            tg_user_id=int(owner_tg_user_id),
            place_id=int(place_id),
            tier="light",
            external_payment_id=ext_payment_id,
            result="success",
        )
        _assert(bool(applied.get("applied")), "mock success payment must be applied")

        resident_places = await get_places_by_service_with_likes(int(service_id))
        resident_row = next((r for r in resident_places if int(r.get("id") or 0) == int(place_id)), None)
        _assert(resident_row is not None, "published place must be present in resident catalog")
        _assert("is_verified" not in resident_row, "resident base query must not include business metadata keys")

        off_rows = await noop.enrich_places_for_main_bot([dict(item) for item in resident_places])
        off_row = next((r for r in off_rows if int(r.get("id") or 0) == int(place_id)), None)
        _assert(off_row is not None, "OFF path must retain target row")
        _assert("is_verified" not in off_row, "OFF path must not inject is_verified")
        _assert("verified_tier" not in off_row, "OFF path must not inject verified_tier")
        _assert("business_enabled" not in off_row, "OFF path must not inject business_enabled")

        on_rows = await integration.enrich_places_for_main_bot([dict(item) for item in resident_places])
        on_row = next((r for r in on_rows if int(r.get("id") or 0) == int(place_id)), None)
        _assert(on_row is not None, "ON path must retain target row")
        _assert(int(on_row.get("is_verified") or 0) == 1, "ON path must inject is_verified=1")
        _assert(str(on_row.get("verified_tier") or "") == "light", "ON path must inject verified_tier=light")
        _assert(int(on_row.get("business_enabled") or 0) == 1, "ON path must inject business_enabled=1")

        print("OK: business mode catalog compare smoke passed.")
    finally:
        await repository.set_place_published(int(place_id), is_published=0)
        await repository.delete_place_draft(int(place_id))


if __name__ == "__main__":
    asyncio.run(main())
