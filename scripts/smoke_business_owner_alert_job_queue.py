#!/usr/bin/env python3
"""
Smoke-check: business owner-request alert is enqueued via admin_jobs queue.

What it validates:
- notify_admins_about_owner_request() creates `admin_owner_request_alert` job
- payload includes expected owner/place identifiers
- no direct dependency on businessbot message delivery path

Run:
  python3 scripts/smoke_business_owner_alert_job_queue.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import aiosqlite


def _setup_import_path() -> None:
    for candidate in (
        Path.cwd() / "src",
        Path("/app/src"),
    ):
        if candidate.exists():
            sys.path.insert(0, str(candidate))
            return


_setup_import_path()

from business.handlers import notify_admins_about_owner_request  # noqa: E402
from config import DB_PATH  # noqa: E402
from database import finish_admin_job, open_db  # noqa: E402


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


async def _select_max_job_id() -> int:
    async with open_db() as db:
        async with db.execute("SELECT COALESCE(MAX(id), 0) FROM admin_jobs") as cur:
            row = await cur.fetchone()
    return int(row[0] or 0) if row else 0


async def _find_alert_job(*, after_id: int, source_marker: str) -> tuple[int, str, dict] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=5000;")
        async with db.execute(
            """
            SELECT id, status, payload_json
            FROM admin_jobs
            WHERE id > ? AND kind = 'admin_owner_request_alert'
            ORDER BY id DESC
            LIMIT 50
            """,
            (int(after_id),),
        ) as cur:
            rows = await cur.fetchall()
    for row in rows:
        payload = json.loads(row[2]) if row[2] else {}
        if str(payload.get("source") or "") == source_marker:
            return int(row[0]), str(row[1] or ""), payload
    return None


async def main() -> None:
    marker = f"smoke_owner_alert_{int(time.time() * 1000)}"
    before_id = await _select_max_job_id()

    fake_user = SimpleNamespace(
        username="smoke_owner",
        first_name="Smoke",
        last_name="Owner",
        full_name="Smoke Owner",
    )
    fake_message = SimpleNamespace(from_user=fake_user)

    owner_id = int(time.time()) % 100000 + 900000
    place_id = 800000 + (int(time.time()) % 10000)
    owner_tg_user_id = 700000 + (int(time.time()) % 10000)

    owner_row = {
        "id": owner_id,
        "place_id": place_id,
        "tg_user_id": owner_tg_user_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    place_row = {"name": "Smoke owner alert place"}

    await notify_admins_about_owner_request(
        fake_message,
        owner_row=owner_row,
        place_row=place_row,
        source=marker,
    )

    found: tuple[int, str, dict] | None = None
    for _ in range(30):
        found = await _find_alert_job(after_id=before_id, source_marker=marker)
        if found:
            break
        await asyncio.sleep(0.1)

    _assert(found is not None, "admin_owner_request_alert job was not enqueued")
    job_id, status, payload = found

    _assert(int(payload.get("request_id") or 0) == owner_id, "payload.request_id mismatch")
    _assert(int(payload.get("place_id") or 0) == place_id, "payload.place_id mismatch")
    _assert(int(payload.get("owner_tg_user_id") or 0) == owner_tg_user_id, "payload.owner_tg_user_id mismatch")
    _assert(str(payload.get("place_name") or "") == "Smoke owner alert place", "payload.place_name mismatch")
    _assert(str(payload.get("source") or "") == marker, "payload.source marker mismatch")

    # Best-effort cleanup: if worker has not processed yet, mark as canceled.
    if status in {"pending", "running"}:
        await finish_admin_job(
            int(job_id),
            status="canceled",
            error="smoke cleanup",
            progress_current=0,
            progress_total=0,
        )

    print("OK: business owner-request alert queue smoke passed.")


if __name__ == "__main__":
    asyncio.run(main())
