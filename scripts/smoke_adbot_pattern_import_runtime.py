#!/usr/bin/env python3
"""
Runtime smoke: anonymized adbot pattern import + review report generation.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
IMPORT_SCRIPT = REPO_ROOT / "scripts" / "import_adbot_pattern_signatures.py"
REPORT_SCRIPT = REPO_ROOT / "scripts" / "generate_adbot_pattern_review_report.py"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS adbot_pattern_signatures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            intent TEXT NOT NULL,
            pattern TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            lang TEXT DEFAULT NULL,
            confidence_floor INTEGER NOT NULL DEFAULT 0,
            source_chat_tag TEXT DEFAULT NULL,
            stats_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            UNIQUE(intent, pattern, source_chat_tag)
        );
        CREATE TABLE IF NOT EXISTS adbot_pattern_stats_daily (
            date TEXT NOT NULL,
            chat_tag TEXT NOT NULL,
            intent TEXT NOT NULL,
            matched INTEGER NOT NULL DEFAULT 0,
            unmatched INTEGER NOT NULL DEFAULT 0,
            fp_flags INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (date, chat_tag, intent)
        );
        """
    )
    conn.commit()


def main() -> None:
    _assert(IMPORT_SCRIPT.exists(), f"file not found: {IMPORT_SCRIPT}")
    _assert(REPORT_SCRIPT.exists(), f"file not found: {REPORT_SCRIPT}")

    with tempfile.TemporaryDirectory(prefix="adbot-pattern-smoke-") as td:
        tmp = Path(td)
        db_path = tmp / "state.db"
        input_json = tmp / "patterns.json"
        review_md = tmp / "review.md"

        conn = sqlite3.connect(str(db_path))
        try:
            _init_schema(conn)
        finally:
            conn.close()

        payload = {
            "signatures": [
                {
                    "intent": "electrician_contact",
                    "pattern": "номер електрика",
                    "weight": 1.0,
                    "lang": "uk",
                    "confidence_floor": 120,
                    "source_chat_tag": "newcastle",
                    "stats_count": 2,
                },
                {
                    # duplicate for upsert increment check
                    "intent": "electrician_contact",
                    "pattern": "номер електрика",
                    "weight": 1.1,
                    "lang": "uk",
                    "confidence_floor": 125,
                    "source_chat_tag": "newcastle",
                    "stats_count": 3,
                },
            ],
            "stats_daily": [
                {
                    "date": "2026-02-24",
                    "chat_tag": "newcastle",
                    "intent": "electrician_contact",
                    "matched": 5,
                    "unmatched": 1,
                    "fp_flags": 0,
                }
            ],
        }
        input_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        subprocess.run(
            [sys.executable, str(IMPORT_SCRIPT), "--db-path", str(db_path), "--input-json", str(input_json)],
            cwd=str(REPO_ROOT),
            check=True,
        )

        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                """
                SELECT stats_count, weight, confidence_floor
                FROM adbot_pattern_signatures
                WHERE intent=? AND pattern=? AND source_chat_tag=?
                """,
                ("electrician_contact", "номер електрика", "newcastle"),
            ).fetchone()
            _assert(row is not None, "signature row must exist after import")
            _assert(int(row[0]) == 5, f"stats_count must be aggregated to 5, got {row[0]}")
        finally:
            conn.close()

        subprocess.run(
            [sys.executable, str(REPORT_SCRIPT), "--db-path", str(db_path), "--output-md", str(review_md)],
            cwd=str(REPO_ROOT),
            check=True,
        )
        _assert(review_md.exists(), "review report must be generated")
        report_text = review_md.read_text(encoding="utf-8")
        _assert("electrician_contact" in report_text, "review report must include imported intent")

    print("OK: adbot pattern import runtime smoke passed.")


if __name__ == "__main__":
    main()

