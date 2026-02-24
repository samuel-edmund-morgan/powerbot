#!/usr/bin/env python3
"""
Policy smoke: adbot pattern mining storage must stay anonymized.

Enforces:
- schema has required adbot pattern tables;
- table definitions do not contain raw-text or user-id columns;
- import/report scripts are present.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_FILE = REPO_ROOT / "schema.sql"
DB_FILE = REPO_ROOT / "src" / "database.py"
IMPORT_SCRIPT = REPO_ROOT / "scripts" / "import_adbot_pattern_signatures.py"
REPORT_SCRIPT = REPO_ROOT / "scripts" / "generate_adbot_pattern_review_report.py"
DISCOVERY_SCRIPT = REPO_ROOT / "scripts" / "discover_adbot_building_chats.py"

FORBIDDEN_COLUMNS = {
    "raw_text",
    "message_text",
    "user_id",
    "username",
    "reporter_tg_user_id",
}


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _extract_table_block(schema_text: str, table_name: str) -> str:
    pattern = re.compile(
        rf"CREATE TABLE IF NOT EXISTS {re.escape(table_name)}\s*\((.*?)\);",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(schema_text)
    return match.group(1) if match else ""


def _extract_column_names(block: str) -> set[str]:
    names: set[str] = set()
    for line in block.splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith(("PRIMARY KEY", "UNIQUE(", "FOREIGN KEY", "CONSTRAINT", "CHECK(")):
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s+", stripped)
        if match:
            names.add(match.group(1).casefold())
    return names


def main() -> None:
    _assert(SCHEMA_FILE.exists(), f"file not found: {SCHEMA_FILE}")
    _assert(DB_FILE.exists(), f"file not found: {DB_FILE}")
    _assert(IMPORT_SCRIPT.exists(), f"file not found: {IMPORT_SCRIPT}")
    _assert(REPORT_SCRIPT.exists(), f"file not found: {REPORT_SCRIPT}")
    _assert(DISCOVERY_SCRIPT.exists(), f"file not found: {DISCOVERY_SCRIPT}")

    schema = SCHEMA_FILE.read_text(encoding="utf-8")
    db_text = DB_FILE.read_text(encoding="utf-8")

    for table_name in ("adbot_pattern_signatures", "adbot_pattern_stats_daily"):
        block = _extract_table_block(schema, table_name)
        _assert(block, f"schema missing table definition for `{table_name}`")
        columns = _extract_column_names(block)
        for column in FORBIDDEN_COLUMNS:
            _assert(
                column.casefold() not in columns,
                f"`{table_name}` must not contain forbidden column `{column}`",
            )

    # Runtime init_db must also own these tables.
    _assert(
        "CREATE TABLE IF NOT EXISTS adbot_pattern_signatures" in db_text,
        "database.init_db must create adbot_pattern_signatures",
    )
    _assert(
        "CREATE TABLE IF NOT EXISTS adbot_pattern_stats_daily" in db_text,
        "database.init_db must create adbot_pattern_stats_daily",
    )

    print("OK: adbot pattern privacy policy smoke passed.")


if __name__ == "__main__":
    main()
