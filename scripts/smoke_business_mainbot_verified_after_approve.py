#!/usr/bin/env python3
"""
Smoke-check: after owner approval + successful payment, main-bot sees verified metadata.

Flow:
1) owner registers new business (unpublished, pending)
2) admin approves owner request (place becomes published)
3) owner performs mock successful payment (light tier)
4) resident-facing places query + business enrich includes verified flags

Run:
  python3 scripts/smoke_business_mainbot_verified_after_approve.py
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

from business import get_business_service  # noqa: E402
from business.repository import BusinessRepository  # noqa: E402
from business.service import BusinessCabinetService  # noqa: E402
from database import get_places_by_service_with_likes  # noqa: E402


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


async def _pick_service_id(repository: BusinessRepository) -> int:
    services = await repository.list_services()
    if services:
        return int(services[0]["id"])
    return await repository.get_or_create_service_id("__smoke_mainbot_verified__")


async def main() -> None:
    repository = BusinessRepository()
    service = BusinessCabinetService(repository=repository)

    admin_id = next(iter(service.admin_ids), None) or 1
    service.admin_ids.add(int(admin_id))

    stamp = int(time.time())
    owner_tg_user_id = int(f"96{stamp % 100000000:08d}")
    service_id = await _pick_service_id(repository)

    created = await service.register_new_business(
        tg_user_id=owner_tg_user_id,
        service_id=int(service_id),
        place_name=f"Smoke Mainbot Verified Place {stamp}",
        description="Temporary place for main-bot verified smoke test",
        address="Smoke building, section 2",
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

        approved = await service.approve_owner_request(int(admin_id), int(owner_id))
        _assert(str(approved.get("status") or "") == "approved", "owner status must become approved")

        place_after_approve = await repository.get_place(int(place_id))
        _assert(place_after_approve is not None, "approved place must exist")
        _assert(int(place_after_approve.get("is_published") or 0) == 1, "approved place must be published")
        _assert(int(place_after_approve.get("is_verified") or 0) == 0, "place must not be verified before payment")

        # Mock payment success for approved owner.
        intent = await service.create_mock_payment_intent(
            tg_user_id=int(owner_tg_user_id),
            place_id=int(place_id),
            tier="light",
            source="plans",
        )
        ext_payment_id = str(intent.get("external_payment_id") or "")
        _assert(ext_payment_id, "mock intent must have external_payment_id")

        outcome = await service.apply_mock_payment_result(
            tg_user_id=int(owner_tg_user_id),
            place_id=int(place_id),
            tier="light",
            external_payment_id=ext_payment_id,
            result="success",
        )
        _assert(bool(outcome.get("applied")), "mock success payment must be applied")

        place_after_payment = await repository.get_place(int(place_id))
        _assert(place_after_payment is not None, "paid place must exist")
        _assert(int(place_after_payment.get("is_verified") or 0) == 1, "paid place must become verified")
        _assert(str(place_after_payment.get("verified_tier") or "") == "light", "verified_tier must be light")
        _assert(bool(place_after_payment.get("verified_until")), "verified_until must be set")

        # Resident-facing query + enrich should expose verified metadata.
        resident_places = await get_places_by_service_with_likes(int(service_id))
        target = next((row for row in resident_places if int(row.get("id") or 0) == int(place_id)), None)
        _assert(target is not None, "published place must be visible in resident places query")

        enriched = await get_business_service().enrich_places_for_main_bot(resident_places)
        target_enriched = next((row for row in enriched if int(row.get("id") or 0) == int(place_id)), None)
        _assert(target_enriched is not None, "target place must remain in enriched list")
        _assert(int(target_enriched.get("is_verified") or 0) == 1, "enriched place must be verified")
        _assert(str(target_enriched.get("verified_tier") or "") == "light", "enriched verified_tier must be light")
        _assert(int(target_enriched.get("business_enabled") or 0) == 1, "enriched business_enabled must be 1")

        print("OK: business main-bot verified-after-approve smoke passed.")
    finally:
        await repository.set_place_published(int(place_id), is_published=0)
        await repository.delete_place_draft(int(place_id))


if __name__ == "__main__":
    asyncio.run(main())
