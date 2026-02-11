#!/usr/bin/env python3
"""
Business visibility smoke-check:
- newly created business place is unpublished and hidden from resident catalog
- after admin approve, place becomes visible in resident catalog

Run:
  python3 scripts/smoke_business_visibility_publish_gate.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path


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
from database import get_places_by_service_with_likes  # noqa: E402


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


async def main() -> None:
    repository = BusinessRepository()
    service = BusinessCabinetService(repository=repository)

    admin_id = next(iter(service.admin_ids), None) or 1
    service.admin_ids.add(int(admin_id))

    stamp = int(time.time())
    owner_tg_user_id = int(f"94{stamp % 100000000:08d}")

    # Use dedicated smoke category to keep assertions deterministic.
    service_id = await repository.get_or_create_service_id("__smoke_visibility_gate__")

    created = await service.register_new_business(
        tg_user_id=owner_tg_user_id,
        service_id=int(service_id),
        place_name=f"Smoke Visibility Place {stamp}",
        description="Temporary place for publish visibility smoke test",
        address="Smoke building, section 3",
    )

    owner = created.get("owner") or {}
    place = created.get("place") or {}
    owner_id = int(owner.get("id") or 0)
    place_id = int(place.get("id") or 0)
    if owner_id <= 0 or place_id <= 0:
        raise AssertionError(f"invalid created objects: owner={owner}, place={place}")

    try:
        _assert(int(place.get("is_published") or 0) == 0, "new place must start unpublished")

        # Resident catalog query should not return unpublished place.
        before_rows = await get_places_by_service_with_likes(int(service_id))
        before_ids = {int(row.get("id") or 0) for row in before_rows}
        _assert(place_id not in before_ids, "unpublished place leaked into resident catalog")

        approved = await service.approve_owner_request(int(admin_id), int(owner_id))
        _assert(str(approved.get("status") or "") == "approved", "owner status must become approved")

        after_rows = await get_places_by_service_with_likes(int(service_id))
        after_ids = {int(row.get("id") or 0) for row in after_rows}
        _assert(place_id in after_ids, "approved/published place is missing from resident catalog")

        print("OK: business visibility publish gate smoke passed.")
    finally:
        # Keep test DB clean.
        await repository.set_place_published(int(place_id), is_published=0)
        await repository.delete_place_draft(int(place_id))


if __name__ == "__main__":
    asyncio.run(main())
