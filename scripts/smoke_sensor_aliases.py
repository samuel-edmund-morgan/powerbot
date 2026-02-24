#!/usr/bin/env python3
"""
Smoke test: sensor aliases contract (cross-section/cross-building bridge).

Checks:
- check_sensors_timeout() propagates source section state to alias targets.
- light notification routing can target subscribers of alias sections on state change.
- calculate_stats() falls back to unique alias source history when target has no events.
- format_light_status() uses alias-aware totals/history for alias target subscribers.
- Ambiguous alias targets (2+ sources) do not use history fallback.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


REPO_ROOT: Path | None = None
for candidate in (Path.cwd(), Path("/app")):
    if (candidate / "src" / "database.py").exists() and (candidate / "src" / "services.py").exists():
        REPO_ROOT = candidate
        break
if REPO_ROOT is None:
    raise RuntimeError("Cannot locate repo root (src/database.py + src/services.py).")

sys.path.insert(0, str(REPO_ROOT / "src"))


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


async def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-sensor-aliases-"))
    db_path = tmpdir / "state.db"

    old_db_path = os.environ.get("DB_PATH")
    os.environ["DB_PATH"] = str(db_path)

    try:
        # Import only after DB_PATH override.
        import database  # noqa: WPS433,E402
        import services  # noqa: WPS433,E402
        import weather  # noqa: WPS433,E402

        await database.init_db()

        # Keep test fully deterministic/offline.
        old_aliases = dict(getattr(services.CFG, "sensor_aliases", {}) or {})
        old_yasno_enabled = bool(getattr(services.CFG, "yasno_enabled", False))
        old_get_weather_line = weather.get_weather_line

        async def _fake_weather_line() -> str:
            return "🌡 Погода: тест"

        services.CFG.yasno_enabled = False
        weather.get_weather_line = _fake_weather_line

        try:
            # Unique alias mapping:
            # source Ньюкасл секція 2 -> Ньюкасл секція 1 + Ньюкасл секція 3 + Брістоль секція 3.
            services.CFG.sensor_aliases = {(1, 2): [(1, 1), (1, 3), (5, 3)]}

            # Alias source is physically online.
            await database.upsert_sensor_heartbeat(
                "smoke-alias-src",
                1,
                2,
                "Smoke alias source",
                None,
            )

            # Add source events with clear non-zero windows for stats.
            now = datetime.now()
            down_at = (now - timedelta(hours=2)).isoformat()
            up_at = (now - timedelta(hours=1)).isoformat()
            async with database.open_db() as db:
                await db.execute(
                    """
                    INSERT INTO events(event_type, timestamp, building_id, section_id)
                    VALUES(?, ?, ?, ?)
                    """,
                    ("down", down_at, 1, 2),
                )
                await db.execute(
                    """
                    INSERT INTO events(event_type, timestamp, building_id, section_id)
                    VALUES(?, ?, ?, ?)
                    """,
                    ("up", up_at, 1, 2),
                )
                await db.commit()

            # Subscribers for source and alias target sections.
            chat_id_alias_cross = 909001  # building 5 section 3 (cross-building alias target)
            chat_id_alias_same_1 = 909002  # building 1 section 1 (same-building alias target)
            chat_id_source = 909003  # building 1 section 2 (source section)
            chat_id_alias_same_3 = 909004  # building 1 section 3 (same-building alias target)

            await database.add_subscriber(chat_id_alias_cross, username="alias_cross", first_name="Alias Cross")
            _assert(await database.set_subscriber_building(chat_id_alias_cross, 5), "failed to set subscriber building (cross)")
            _assert(await database.set_subscriber_section(chat_id_alias_cross, 3), "failed to set subscriber section (cross)")

            await database.add_subscriber(chat_id_alias_same_1, username="alias_same_1", first_name="Alias Same 1")
            _assert(await database.set_subscriber_building(chat_id_alias_same_1, 1), "failed to set subscriber building (same-1)")
            _assert(await database.set_subscriber_section(chat_id_alias_same_1, 1), "failed to set subscriber section (same-1)")

            await database.add_subscriber(chat_id_source, username="alias_source", first_name="Alias Source")
            _assert(await database.set_subscriber_building(chat_id_source, 1), "failed to set subscriber building (source)")
            _assert(await database.set_subscriber_section(chat_id_source, 2), "failed to set subscriber section (source)")

            await database.add_subscriber(chat_id_alias_same_3, username="alias_same_3", first_name="Alias Same 3")
            _assert(await database.set_subscriber_building(chat_id_alias_same_3, 1), "failed to set subscriber building (same-3)")
            _assert(await database.set_subscriber_section(chat_id_alias_same_3, 3), "failed to set subscriber section (same-3)")

            # 1) Alias propagation in live state.
            current_states = await services.check_sensors_timeout()
            _assert(current_states.get((1, 2)) is True, "source section must be UP")
            _assert(current_states.get((1, 1)) is True, "same-building alias target section 1 must be UP via source")
            _assert(current_states.get((1, 3)) is True, "same-building alias target section 3 must be UP via source")
            _assert(current_states.get((5, 3)) is True, "cross-building alias target section must be UP via source")

            # 1b) Notification-routing contract for alias sections:
            # when source heartbeat becomes stale, all alias target states should change too,
            # and per-(building,section) recipient lists should match section subscribers.
            stale_at = (datetime.now() - timedelta(minutes=10)).isoformat()
            async with database.open_db() as db:
                await db.execute("UPDATE sensors SET last_heartbeat = ? WHERE uuid = ?", (stale_at, "smoke-alias-src"))
                await db.commit()

            next_states = await services.check_sensors_timeout()
            expected_changed = {(1, 1), (1, 2), (1, 3), (5, 3)}
            changed = {
                key for key in expected_changed
                if current_states.get(key) != next_states.get(key)
            }
            _assert(changed == expected_changed, f"alias state-change set mismatch: {changed} vs {expected_changed}")

            current_hour = datetime.now().hour
            recipients_1_1 = set(await database.get_subscribers_for_light_notification(current_hour, 1, 1))
            recipients_1_2 = set(await database.get_subscribers_for_light_notification(current_hour, 1, 2))
            recipients_1_3 = set(await database.get_subscribers_for_light_notification(current_hour, 1, 3))
            recipients_5_3 = set(await database.get_subscribers_for_light_notification(current_hour, 5, 3))

            _assert(chat_id_alias_same_1 in recipients_1_1, "section 1 notification recipients must include section-1 subscriber")
            _assert(chat_id_source in recipients_1_2, "source section notification recipients must include section-2 subscriber")
            _assert(chat_id_alias_same_3 in recipients_1_3, "section 3 notification recipients must include section-3 subscriber")
            _assert(chat_id_alias_cross in recipients_5_3, "cross-building alias section recipients must include target subscriber")

            _assert(chat_id_source not in recipients_1_1, "section 1 recipients must not include section-2 subscriber")
            _assert(chat_id_alias_same_1 not in recipients_1_3, "section 3 recipients must not include section-1 subscriber")
            _assert(chat_id_alias_cross not in recipients_1_2, "source section recipients must not include cross-building subscriber")

            # 2) Stats fallback for unique alias source.
            src_stats = await services.calculate_stats(period_days=1, building_id=1, section_id=2)
            dst_stats = await services.calculate_stats(period_days=1, building_id=5, section_id=3)
            for key in ("total_uptime", "total_downtime"):
                diff = abs(float(src_stats[key]) - float(dst_stats[key]))
                _assert(diff < 5.0, f"stats mismatch for {key}: src={src_stats[key]} dst={dst_stats[key]}")

            # 3) format_light_status should use alias virtual totals + non-zero history.
            text = await services.format_light_status(chat_id_alias_cross)
            _assert(
                "Стан електропостачання в Брістоль секція 3" in text,
                "expected alias target heading in status text",
            )
            _assert(
                "секція: 1/1, будинок: 1/1" in text,
                "expected virtual alias sensor totals in status text",
            )
            _assert(
                "📊 Сьогодні: ✅ 0 сек | ❌ 0 сек" not in text,
                "alias history fallback should prevent zeroed stats for target",
            )

            # 4) Ambiguous alias target must NOT fallback to any source history.
            services.CFG.sensor_aliases = {(1, 2): [(5, 3)], (2, 1): [(5, 3)]}
            ambiguous_stats = await services.calculate_stats(period_days=1, building_id=5, section_id=3)
            total = float(ambiguous_stats["total_uptime"]) + float(ambiguous_stats["total_downtime"])
            _assert(
                total < 0.001,
                "ambiguous alias target must not use source history fallback",
            )

            print("OK: sensor aliases smoke passed.")
        finally:
            services.CFG.sensor_aliases = old_aliases
            services.CFG.yasno_enabled = old_yasno_enabled
            weather.get_weather_line = old_get_weather_line
    finally:
        if old_db_path is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = old_db_path
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
