#!/usr/bin/env python3
"""
Smoke-check: "stealth" businessbot rollout.

Goal:
- BUSINESS_MODE=0 must keep resident UI/logic disabled (NoopBusinessService).
- businessbot runtime must be considered enabled when BUSINESS_BOT_API_KEY is set,
  even if BUSINESS_MODE=0 (so we can run businessbot in prod while keeping resident UI legacy).

Run:
  BUSINESS_MODE=0 BUSINESS_BOT_API_KEY=dummy python3 scripts/smoke_businessbot_stealth.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


os.environ["BUSINESS_MODE"] = "0"
os.environ["BUSINESS_BOT_API_KEY"] = os.environ.get("BUSINESS_BOT_API_KEY") or "000000000:dummy"

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
    _assert(is_business_mode_enabled() is False, "BUSINESS_MODE must be disabled for stealth rollout")
    _assert(is_business_feature_enabled() is False, "resident business feature guard must be disabled")
    _assert(is_business_bot_enabled() is True, "businessbot must be enabled when BUSINESS_BOT_API_KEY is set")

    svc = get_business_service()
    _assert(type(svc).__name__ == "NoopBusinessService", f"unexpected service class: {type(svc).__name__}")

    print("OK: businessbot stealth smoke passed.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())

