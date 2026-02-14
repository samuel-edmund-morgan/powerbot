#!/usr/bin/env python3
"""
Smoke test: "Sections (1..3) per building" schema + migration/backfill.

This script creates a legacy-like SQLite DB (without section_id columns),
runs migrate_db.py logic against the current schema.sql, then asserts:
- PRAGMA integrity_check == ok
- new section-aware columns/tables exist
- backfills set default section_id:
  - building_id=1 (Newcastle) -> section_id=2
  - other buildings -> section_id=1
- legacy events get bound to (building_id=1, section_id=2)

Run:
  python3 scripts/smoke_sections.py
"""

from __future__ import annotations

import sys
import shutil
import sqlite3
import tempfile
from pathlib import Path

# Support execution via stdin inside container and local file execution.
REPO_ROOT: Path | None = None
for candidate in (
    Path.cwd(),   # local repo root or container WORKDIR (/app)
    Path("/app"), # container fallback
):
    if (candidate / "schema.sql").exists() and (candidate / "migrate_db.py").exists():
        REPO_ROOT = candidate
        sys.path.insert(0, str(candidate))
        break
if REPO_ROOT is None:
    raise RuntimeError("Cannot locate repo root (schema.sql + migrate_db.py).")

import migrate_db  # noqa: E402
from migrate_db import DatabaseMigrator, prepare_schema_db  # noqa: E402


def _create_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE subscribers (
                chat_id INTEGER PRIMARY KEY,
                quiet_start INTEGER DEFAULT NULL,
                quiet_end INTEGER DEFAULT NULL,
                username TEXT DEFAULT NULL,
                first_name TEXT DEFAULT NULL,
                subscribed_at TEXT DEFAULT NULL,
                light_notifications INTEGER DEFAULT 1,
                alert_notifications INTEGER DEFAULT 1,
                schedule_notifications INTEGER DEFAULT 1,
                building_id INTEGER DEFAULT NULL
            );

            CREATE TABLE sensors (
                uuid TEXT PRIMARY KEY,
                building_id INTEGER NOT NULL,
                name TEXT DEFAULT NULL,
                last_heartbeat TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1
            );

            CREATE TABLE heating_votes (
                chat_id INTEGER PRIMARY KEY,
                has_heating INTEGER NOT NULL,
                voted_at TEXT NOT NULL,
                building_id INTEGER DEFAULT NULL
            );

            CREATE TABLE water_votes (
                chat_id INTEGER PRIMARY KEY,
                has_water INTEGER NOT NULL,
                voted_at TEXT NOT NULL,
                building_id INTEGER DEFAULT NULL
            );

            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );
            """
        )

        # Seed minimal legacy data.
        conn.executescript(
            """
            INSERT INTO subscribers(chat_id, building_id, username, first_name)
            VALUES (111, 1, 'u1', 'User 1'),
                   (222, 3, 'u2', 'User 2');

            INSERT INTO sensors(uuid, building_id, name, last_heartbeat, created_at, is_active)
            VALUES ('s1', 1, 'S1', datetime('now'), datetime('now'), 1),
                   ('s2', 3, 'S2', datetime('now'), datetime('now'), 1);

            INSERT INTO heating_votes(chat_id, has_heating, voted_at, building_id)
            VALUES (111, 1, datetime('now'), 1),
                   (222, 0, datetime('now'), 3);

            INSERT INTO water_votes(chat_id, has_water, voted_at, building_id)
            VALUES (111, 1, datetime('now'), 1),
                   (222, 0, datetime('now'), 3);

            INSERT INTO events(event_type, timestamp)
            VALUES ('down', datetime('now','-1 hour')),
                   ('up', datetime('now'));
            """
        )
        conn.commit()
    finally:
        conn.close()


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> None:
    schema_path = REPO_ROOT / "schema.sql"
    _assert(schema_path.exists(), f"schema.sql not found at {schema_path}")

    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-sections-"))
    try:
        # migrate_db.py uses a module-level BACKUP_DIR; point it at our tmpdir.
        migrate_db.BACKUP_DIR = tmpdir

        target_db = tmpdir / "state.db"
        _create_legacy_db(target_db)

        source_db = prepare_schema_db(str(schema_path))
        migrator = DatabaseMigrator(
            dry_run=False,
            verbose=False,
            source_db=source_db,
            target_db=target_db,
        )
        ok = migrator.run()
        _assert(ok is True, "migrate_db.DatabaseMigrator.run() returned False")

        conn = sqlite3.connect(target_db)
        try:
            integrity = conn.execute("PRAGMA integrity_check;").fetchone()
            _assert(integrity and integrity[0] == "ok", f"integrity_check failed: {integrity}")

            _assert("section_id" in _column_names(conn, "subscribers"), "subscribers.section_id missing")
            _assert("section_id" in _column_names(conn, "sensors"), "sensors.section_id missing")
            _assert("comment" in _column_names(conn, "sensors"), "sensors.comment missing")
            _assert("section_id" in _column_names(conn, "heating_votes"), "heating_votes.section_id missing")
            _assert("section_id" in _column_names(conn, "water_votes"), "water_votes.section_id missing")
            _assert("building_id" in _column_names(conn, "events"), "events.building_id missing")
            _assert("section_id" in _column_names(conn, "events"), "events.section_id missing")

            # Backfills.
            subs = conn.execute(
                "SELECT chat_id, building_id, section_id FROM subscribers ORDER BY chat_id"
            ).fetchall()
            _assert(subs == [(111, 1, 2), (222, 3, 1)], f"unexpected subscribers backfill: {subs}")

            sensors = conn.execute(
                "SELECT uuid, building_id, section_id, comment FROM sensors ORDER BY uuid"
            ).fetchall()
            _assert(
                sensors == [("s1", 1, 2, None), ("s2", 3, 1, None)],
                f"unexpected sensors backfill: {sensors}",
            )

            hv = conn.execute(
                "SELECT chat_id, building_id, section_id FROM heating_votes ORDER BY chat_id"
            ).fetchall()
            _assert(hv == [(111, 1, 2), (222, 3, 1)], f"unexpected heating_votes backfill: {hv}")

            wv = conn.execute(
                "SELECT chat_id, building_id, section_id FROM water_votes ORDER BY chat_id"
            ).fetchall()
            _assert(wv == [(111, 1, 2), (222, 3, 1)], f"unexpected water_votes backfill: {wv}")

            ev = conn.execute(
                "SELECT id, event_type, building_id, section_id FROM events ORDER BY id"
            ).fetchall()
            _assert(
                ev[0][2:] == (1, 2) and ev[1][2:] == (1, 2),
                f"unexpected legacy events backfill: {ev}",
            )

            # Regression test: Some buildings have only 2 sections; section_id=3 must be clamped to 1.
            conn.execute("UPDATE subscribers SET section_id=3 WHERE chat_id=222")
            conn.execute("UPDATE heating_votes SET section_id=3 WHERE chat_id=222")
            conn.execute("UPDATE water_votes SET section_id=3 WHERE chat_id=222")
            conn.commit()
        finally:
            conn.close()

        # Run migration again with schema already up-to-date; backfills must still run.
        migrator2 = DatabaseMigrator(
            dry_run=False,
            verbose=False,
            source_db=source_db,
            target_db=target_db,
        )
        ok2 = migrator2.run()
        _assert(ok2 is True, "migrate_db.DatabaseMigrator.run() second pass returned False")

        conn = sqlite3.connect(target_db)
        try:
            subs2 = conn.execute(
                "SELECT chat_id, building_id, section_id FROM subscribers WHERE chat_id=222"
            ).fetchone()
            _assert(subs2 == (222, 3, 1), f"unexpected subscribers clamp: {subs2}")

            hv2 = conn.execute(
                "SELECT chat_id, building_id, section_id FROM heating_votes WHERE chat_id=222"
            ).fetchone()
            _assert(hv2 == (222, 3, 1), f"unexpected heating_votes clamp: {hv2}")

            wv2 = conn.execute(
                "SELECT chat_id, building_id, section_id FROM water_votes WHERE chat_id=222"
            ).fetchone()
            _assert(wv2 == (222, 3, 1), f"unexpected water_votes clamp: {wv2}")

            print("OK: sections schema/migration smoke test passed.")
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
