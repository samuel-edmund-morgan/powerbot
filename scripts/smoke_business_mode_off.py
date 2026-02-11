#!/usr/bin/env python3
"""
Smoke-check: business feature isolation when BUSINESS_MODE=0.

What it validates:
- business feature flag is disabled
- business bot is not considered enabled
- main-bot integration uses NoopBusinessService
- place enrichment is no-op (no unexpected metadata injection)

Run:
  BUSINESS_MODE=0 BUSINESS_BOT_API_KEY= python3 scripts/smoke_business_mode_off.py
"""

from __future__ import annotations

import os
import sys
from copy import deepcopy
from pathlib import Path


# Force disabled mode before importing project modules.
os.environ["BUSINESS_MODE"] = "0"
os.environ["BUSINESS_BOT_API_KEY"] = ""

# Support execution via stdin inside container and local file execution.
for candidate in (
    Path.cwd() / "src",   # repo root local
    Path("/app/src"),     # container
):
    if candidate.exists():
        sys.path.insert(0, str(candidate))
        break

from config import is_business_bot_enabled, is_business_mode_enabled  # noqa: E402
from business import get_business_service, is_business_feature_enabled  # noqa: E402


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


async def _main() -> None:
    _assert(is_business_mode_enabled() is False, "BUSINESS_MODE must be disabled")
    _assert(is_business_feature_enabled() is False, "business feature guard must be disabled")
    _assert(is_business_bot_enabled() is False, "business bot must be disabled without token/flag")

    svc = get_business_service()
    _assert(type(svc).__name__ == "NoopBusinessService", f"unexpected service class: {type(svc).__name__}")

    places = [
        {"id": 1, "name": "A", "likes_count": 5},
        {"id": 2, "name": "B", "likes_count": 0},
    ]
    original = deepcopy(places)
    enriched = await svc.enrich_places_for_main_bot(places)
    _assert(enriched == original, "NoopBusinessService enrichment must not modify places")

    print("OK: business mode off smoke passed.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
