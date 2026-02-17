#!/usr/bin/env python3
"""
Smoke test: derived buildings.has_sensor / buildings.sensor_count stay in sync.

Verifies:
- init_db performs full resync from sensors.
- upsert/move/reactivate/deactivate sensor flows keep counters accurate.
- manual sync helper can recover stale values.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


REPO_ROOT: Path | None = None
for candidate in (Path.cwd(), Path("/app")):
    if (candidate / "src" / "database.py").exists() and (candidate / "schema.sql").exists():
        REPO_ROOT = candidate
        break
if REPO_ROOT is None:
    raise RuntimeError("Cannot locate repo root (src/database.py + schema.sql).")

sys.path.insert(0, str(REPO_ROOT / "src"))


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


async def _check(database_module, building_id: int, expected_count: int) -> None:
    info = await database_module.get_building_info(building_id)
    _assert(info is not None, f"building {building_id} not found")
    _assert(
        int(info["sensor_count"]) == int(expected_count),
        f"building {building_id}: expected sensor_count={expected_count}, got {info['sensor_count']}",
    )
    _assert(
        bool(info["has_sensor"]) == (expected_count > 0),
        f"building {building_id}: expected has_sensor={expected_count > 0}, got {info['has_sensor']}",
    )


async def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-building-sensor-stats-"))
    db_path = tmpdir / "state.db"
    try:
        os.environ["DB_PATH"] = str(db_path)
        # Import only after DB_PATH override.
        import database  # noqa: WPS433,E402

        await database.init_db()

        # Fresh DB: no active sensors yet.
        await _check(database, 1, 0)
        await _check(database, 2, 0)

        # New sensor -> counters increase.
        is_new = await database.upsert_sensor_heartbeat("smoke-s1", 1, 2, "S1", None)
        _assert(is_new is True, "expected first upsert to create sensor")
        await _check(database, 1, 1)

        # Heartbeat update (same sensor/same building) must not change counters.
        is_new_again = await database.upsert_sensor_heartbeat("smoke-s1", 1, 2, "S1", None)
        _assert(is_new_again is False, "expected second upsert to update sensor")
        await _check(database, 1, 1)

        # Sensor moved to another building -> both counters update.
        await database.upsert_sensor_heartbeat("smoke-s1", 2, 1, "S1", None)
        await _check(database, 1, 0)
        await _check(database, 2, 1)

        # Add second sensor in building 2.
        await database.upsert_sensor_heartbeat("smoke-s2", 2, 1, "S2", None)
        await _check(database, 2, 2)

        # Deactivate one sensor -> counters decrease.
        await database.deactivate_sensor("smoke-s2")
        await _check(database, 2, 1)

        # Reactivate the same sensor via heartbeat.
        await database.upsert_sensor_heartbeat("smoke-s2", 2, 1, "S2", None)
        await _check(database, 2, 2)

        # Manual recovery helper for stale values.
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("UPDATE buildings SET has_sensor=1, sensor_count=999 WHERE id=2")
            conn.commit()
        finally:
            conn.close()

        await database.sync_building_sensor_stats(2)
        await _check(database, 2, 2)

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("UPDATE buildings SET has_sensor=1, sensor_count=777")
            conn.commit()
        finally:
            conn.close()

        await database.sync_building_sensor_stats()
        await _check(database, 1, 0)
        await _check(database, 2, 2)

        print("OK: buildings sensor stats sync smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
