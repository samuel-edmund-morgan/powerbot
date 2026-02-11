#!/usr/bin/env python3
"""
Business claim-token smoke-check (admin UI flow core logic).

What it validates:
- active token can be created for a place
- token rotation revokes previous token and creates a new active token
- claim consumes token and marks it as used
- used token cannot be claimed again

Run:
  python3 scripts/smoke_business_claim_tokens.py
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
from business.service import BusinessCabinetService, ValidationError  # noqa: E402


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


async def main() -> None:
    repository = BusinessRepository()
    service = BusinessCabinetService(repository=repository)

    # Ensure we have an admin id for service guard checks in smoke context.
    admin_id = next(iter(service.admin_ids), None) or 1
    service.admin_ids.add(int(admin_id))

    stamp = int(time.time())
    service_id = await repository.get_or_create_service_id("__smoke_claim_tokens__")
    place_id = await repository.create_place(
        service_id=service_id,
        name=f"Smoke Claim Place {stamp}",
        description="Temporary place for claim token smoke test",
        address="Smoke building, section 1",
    )

    owner_id = int(f"91{stamp % 100000000:08d}")

    try:
        created = await service.get_or_create_active_claim_token_for_place(int(admin_id), int(place_id))
        token_row = created.get("token_row") or {}
        token_first = str(token_row.get("token") or "")
        _assert(token_first, "initial token must be non-empty")

        rotated = await service.rotate_claim_token_for_place(int(admin_id), int(place_id))
        token_second = str(rotated.get("token") or "")
        _assert(token_second, "rotated token must be non-empty")
        _assert(token_second != token_first, "rotated token must differ from initial token")

        first_row = await repository.get_claim_token(token_first)
        _assert(first_row is not None, "first token row must exist")
        _assert(str(first_row.get("status")) == "revoked", "first token must be revoked after rotation")

        second_row = await repository.get_claim_token(token_second)
        _assert(second_row is not None, "second token row must exist")
        _assert(str(second_row.get("status")) == "active", "second token must be active before claim")

        claimed = await service.claim_business_by_token(int(owner_id), token_second)
        _assert(str(claimed.get("token") or "") == token_second, "claim must return consumed token")

        second_used = await repository.get_claim_token(token_second)
        _assert(second_used is not None, "second token row must still exist")
        _assert(str(second_used.get("status")) == "used", "second token must be marked used after claim")
        _assert(int(second_used.get("used_by") or 0) == int(owner_id), "used_by must match owner id")

        # Re-claiming used token must fail with validation error.
        try:
            await service.claim_business_by_token(int(owner_id), token_second)
        except ValidationError:
            pass
        else:
            raise AssertionError("used token must not be claimable again")

        print("OK: business claim token smoke passed.")
    finally:
        # Cleanup temporary unpublished place and related business rows.
        await repository.delete_place_and_related_unpublished(int(place_id))


if __name__ == "__main__":
    asyncio.run(main())

