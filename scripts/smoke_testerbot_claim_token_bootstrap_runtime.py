#!/usr/bin/env python3
"""
Runtime smoke for bootstrap_testerbot_claim_token.py.

Validates:
- script seeds one active token when none exists,
- second run is idempotent (does not create another active token for the same place).
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _prepare_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE general_services (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE places (
                id INTEGER PRIMARY KEY,
                service_id INTEGER NOT NULL,
                name TEXT NOT NULL
            );
            CREATE TABLE business_claim_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id INTEGER NOT NULL,
                token TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active',
                attempts_left INTEGER NOT NULL DEFAULT 5,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_by INTEGER DEFAULT NULL,
                used_at TEXT DEFAULT NULL,
                used_by INTEGER DEFAULT NULL
            );
            """
        )
        conn.execute("INSERT INTO general_services(id, name) VALUES (1, 'Alpha')")
        conn.execute("INSERT INTO places(id, service_id, name) VALUES (101, 1, 'Smoke place')")
        conn.commit()
    finally:
        conn.close()


def _count_active_tokens(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM business_claim_tokens WHERE place_id = 101 AND status = 'active'"
        ).fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "bootstrap_testerbot_claim_token.py"

    with tempfile.TemporaryDirectory(prefix="smoke_testerbot_claim_token_") as tmp:
        db_path = Path(tmp) / "state.db"
        _prepare_db(db_path)

        first = subprocess.run(
            [sys.executable, str(script_path), "--db-path", str(db_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        _assert("seeded active token" in first.stdout, f"unexpected first run output: {first.stdout!r}")
        _assert(_count_active_tokens(db_path) == 1, "first run should create one active token")

        second = subprocess.run(
            [sys.executable, str(script_path), "--db-path", str(db_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        _assert(
            "active token already exists" in second.stdout,
            f"unexpected second run output: {second.stdout!r}",
        )
        _assert(_count_active_tokens(db_path) == 1, "second run must be idempotent")

    print("OK: testerbot claim-token bootstrap runtime smoke passed.")


if __name__ == "__main__":
    main()

