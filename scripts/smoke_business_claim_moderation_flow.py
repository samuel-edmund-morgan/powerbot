#!/usr/bin/env python3
"""
Business claim moderation flow smoke-check.

What it validates:
- admin can create/get active claim token for existing place
- owner claim by token creates pending owner request
- admin approve moves request to approved
- place remains published and business is enabled after approve

Run:
  python3 scripts/smoke_business_claim_moderation_flow.py
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


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


async def _pick_service_id(repository: BusinessRepository) -> int:
    services = await repository.list_services()
    if services:
        return int(services[0]["id"])
    return await repository.get_or_create_service_id("__smoke_claim_moderation__")


async def main() -> None:
    repository = BusinessRepository()
    service = BusinessCabinetService(repository=repository)

    admin_id = next(iter(service.admin_ids), None) or 1
    service.admin_ids.add(int(admin_id))

    stamp = int(time.time())
    owner_tg_user_id = int(f"95{stamp % 100000000:08d}")
    service_id = await _pick_service_id(repository)

    # Existing place that owner will claim.
    place_id = await repository.create_place(
        service_id=int(service_id),
        name=f"Smoke Claim Moderation Place {stamp}",
        description="Temporary place for claim moderation smoke test",
        address="Smoke building, section 1",
    )
    await repository.set_place_published(int(place_id), is_published=1)

    try:
        token_bundle = await service.get_or_create_active_claim_token_for_place(int(admin_id), int(place_id))
        token_row = token_bundle.get("token_row") or {}
        token = str(token_row.get("token") or "")
        _assert(token, "claim token must be non-empty")

        claimed = await service.claim_business_by_token(int(owner_tg_user_id), token)
        _assert(str(claimed.get("token") or "") == token, "claim must return consumed token")
        owner = claimed.get("owner") or {}
        owner_id = int(owner.get("id") or 0)
        _assert(owner_id > 0, "claim must create owner request row")
        _assert(str(owner.get("status") or "") == "pending", "claimed owner request must be pending")
        _assert(int(owner.get("place_id") or 0) == int(place_id), "owner request place_id mismatch")

        place_before = await repository.get_place(int(place_id))
        _assert(place_before is not None, "claimed place must exist")
        _assert(int(place_before.get("is_published") or 0) == 1, "existing place must stay published before approve")

        approved = await service.approve_owner_request(int(admin_id), int(owner_id))
        _assert(str(approved.get("status") or "") == "approved", "owner status must become approved")

        place_after = await repository.get_place(int(place_id))
        _assert(place_after is not None, "approved place must exist")
        _assert(int(place_after.get("is_published") or 0) == 1, "claimed place must remain published after approve")
        _assert(int(place_after.get("business_enabled") or 0) == 1, "business_enabled must be 1 after approve")
        _assert(int(place_after.get("is_verified") or 0) == 0, "free subscription must not auto-verify place")

        print("OK: business claim moderation flow smoke passed.")
    finally:
        await repository.set_place_published(int(place_id), is_published=0)
        await repository.delete_place_draft(int(place_id))


if __name__ == "__main__":
    asyncio.run(main())
