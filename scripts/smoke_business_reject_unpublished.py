#!/usr/bin/env python3
"""
Business moderation smoke-check: reject must keep place as unpublished draft.

What it validates:
- new owner request creates unpublished place
- admin reject changes owner status to rejected
- rejected place is not deleted and remains unpublished
- business flags stay disabled after reject
- rejected place is hidden from resident published queries
- rejected place is still visible in admin unpublished filter

Run:
  python3 scripts/smoke_business_reject_unpublished.py
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
import sys


def _setup_import_path() -> None:
    for candidate in (
        Path.cwd() / "src",  # local repo root
        Path("/app/src"),    # container path
    ):
        if candidate.exists():
            sys.path.insert(0, str(candidate))
            return


_setup_import_path()

from business.repository import BusinessRepository  # noqa: E402
from business.service import BusinessCabinetService  # noqa: E402
from database import get_places_by_service  # noqa: E402


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


async def _pick_service_id(repository: BusinessRepository) -> int:
    services = await repository.list_services()
    if services:
        return int(services[0]["id"])
    return await repository.get_or_create_service_id("__smoke_reject_unpublished__")


async def main() -> None:
    repository = BusinessRepository()
    service = BusinessCabinetService(repository=repository)

    admin_id = next(iter(service.admin_ids), None) or 1
    service.admin_ids.add(int(admin_id))

    stamp = int(time.time())
    owner_tg_user_id = int(f"92{stamp % 100000000:08d}")
    service_id = await _pick_service_id(repository)

    created = await service.register_new_business(
        tg_user_id=owner_tg_user_id,
        service_id=int(service_id),
        place_name=f"Smoke Reject Place {stamp}",
        description="Temporary place for reject/unpublished smoke test",
        address="Smoke building, section 1",
    )

    owner = created.get("owner") or {}
    place = created.get("place") or {}
    owner_id = int(owner.get("id") or 0)
    place_id = int(place.get("id") or 0)
    if owner_id <= 0 or place_id <= 0:
        raise AssertionError(f"invalid created objects: owner={owner}, place={place}")

    try:
        _assert(int(place.get("is_published") or 0) == 0, "new place must start unpublished")
        _assert(str(owner.get("status") or "") == "pending", "new owner request must be pending")

        rejected = await service.reject_owner_request(int(admin_id), int(owner_id))
        _assert(str(rejected.get("status") or "") == "rejected", "owner status must become rejected")

        place_after = await repository.get_place(int(place_id))
        _assert(place_after is not None, "rejected place must remain in DB")
        _assert(int(place_after.get("is_published") or 0) == 0, "rejected place must stay unpublished")
        _assert(int(place_after.get("business_enabled") or 0) == 0, "business_enabled must stay 0")
        _assert(int(place_after.get("is_verified") or 0) == 0, "is_verified must stay 0")
        _assert((place_after.get("verified_tier") or None) is None, "verified_tier must be NULL")
        _assert((place_after.get("verified_until") or None) is None, "verified_until must be NULL")

        pending = await repository.get_pending_owner_request_for_place(int(place_id))
        _assert(pending is None, "place must not have pending owner request after reject")

        owner_after = await repository.get_owner_request(int(owner_id))
        _assert(owner_after is not None, "owner row must remain for auditability")
        _assert(str(owner_after.get("status") or "") == "rejected", "owner row status must be rejected")

        # Resident-facing query must not include unpublished rejected place.
        resident_places = await get_places_by_service(int(service_id))
        resident_place_ids = {int(row.get("id") or 0) for row in resident_places}
        _assert(
            int(place_id) not in resident_place_ids,
            "rejected unpublished place must not appear in resident published list",
        )

        # Admin unpublished filter must still include this place.
        admin_unpublished = await repository.list_places_by_service_filtered(
            int(service_id),
            is_published=0,
            limit=200,
            offset=0,
        )
        admin_unpublished_ids = {int(row.get("id") or 0) for row in admin_unpublished}
        _assert(
            int(place_id) in admin_unpublished_ids,
            "rejected unpublished place must stay visible in admin unpublished filter",
        )

        print("OK: business reject->unpublished smoke passed.")
    finally:
        # Keep test DB clean.
        await repository.delete_place_draft(int(place_id))


if __name__ == "__main__":
    asyncio.run(main())
