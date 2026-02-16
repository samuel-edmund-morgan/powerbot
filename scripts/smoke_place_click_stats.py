#!/usr/bin/env python3
"""
Smoke-check: place card click/view stats (DB-backed, not logs).

Validates:
- table place_views_daily exists (migrations/init_db)
- record_place_view() increments daily counter
- business repository summary returns correct top/bottom/total for a category

Run:
  python3 scripts/smoke_place_click_stats.py
"""

from __future__ import annotations

import sys
from pathlib import Path


# Support execution via stdin inside container and local file execution.
for candidate in (
    Path.cwd() / "src",   # repo root local
    Path("/app/src"),     # container
):
    if candidate.exists():
        sys.path.insert(0, str(candidate))
        break

from database import build_keywords, open_db, record_place_view  # noqa: E402
from business.repository import BusinessRepository  # noqa: E402


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


SERVICE_NAME = "SMOKE: Click stats"
PLACE_NAMES = [f"SMOKE Place {idx}" for idx in range(1, 6)]


async def _ensure_service_and_places() -> tuple[int, list[int]]:
    async with open_db() as db:
        await db.execute("INSERT OR IGNORE INTO general_services(name) VALUES(?)", (SERVICE_NAME,))
        await db.commit()

        async with db.execute("SELECT id FROM general_services WHERE name = ?", (SERVICE_NAME,)) as cur:
            row = await cur.fetchone()
            _assert(row is not None, "failed to ensure smoke service")
            service_id = int(row[0])

        place_ids: list[int] = []
        for name in PLACE_NAMES:
            async with db.execute(
                "SELECT id FROM places WHERE service_id = ? AND name = ? LIMIT 1",
                (service_id, name),
            ) as cur:
                row = await cur.fetchone()
                if row:
                    place_ids.append(int(row[0]))
                    continue

            keywords = build_keywords(name, "smoke", None)
            cur = await db.execute(
                """
                INSERT INTO places(service_id, name, description, address, keywords, is_published)
                VALUES(?, ?, ?, ?, ?, 1)
                """,
                (service_id, name, "smoke", "smoke address", keywords),
            )
            await db.commit()
            place_ids.append(int(cur.lastrowid))

        _assert(len(place_ids) == 5, "expected 5 smoke places")

        # Reset counters for these smoke places on all days for deterministic assertions.
        placeholders = ",".join("?" for _ in place_ids)
        await db.execute(
            f"""
            DELETE FROM place_views_daily
             WHERE place_id IN ({placeholders})
            """,
            tuple(place_ids),
        )
        await db.commit()

        return service_id, place_ids


async def _main() -> None:
    service_id, place_ids = await _ensure_service_and_places()

    # Make the ranking deterministic:
    # - place_ids[0] gets 10 views
    # - place_ids[1] gets 2 views
    # - others get 0 views
    for _ in range(10):
        await record_place_view(place_ids[0])
    for _ in range(2):
        await record_place_view(place_ids[1])

    repo = BusinessRepository()
    summary = await repo.get_service_views_summary(service_id, days=30)
    _assert(summary["place_count"] >= 5, f"unexpected place_count: {summary}")
    _assert(summary["top_views"] == 10, f"unexpected top_views: {summary}")
    _assert(summary["bottom_views"] == 0, f"unexpected bottom_views: {summary}")
    _assert(summary["total_views"] == 12, f"unexpected total_views: {summary}")

    own_top = await repo.get_place_views_sum(place_ids[0], days=30)
    own_mid = await repo.get_place_views_sum(place_ids[1], days=30)
    own_zero = await repo.get_place_views_sum(place_ids[2], days=30)
    _assert(own_top == 10, f"unexpected own_top: {own_top}")
    _assert(own_mid == 2, f"unexpected own_mid: {own_mid}")
    _assert(own_zero == 0, f"unexpected own_zero: {own_zero}")

    print("OK: place click stats smoke passed.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
