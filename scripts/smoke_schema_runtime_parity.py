#!/usr/bin/env python3
"""
Static smoke-check: schema.sql vs runtime init_db() parity.

Policy:
- Every `CREATE TABLE IF NOT EXISTS ...` in runtime must exist in schema.sql.
- Every `CREATE TABLE IF NOT EXISTS ...` in schema.sql must exist in runtime.
- Same for `CREATE [UNIQUE] INDEX IF NOT EXISTS ...`.
"""

from __future__ import annotations

import re
from pathlib import Path


TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+([a-zA-Z0-9_]+)", re.IGNORECASE)
INDEX_RE = re.compile(r"CREATE (?:UNIQUE\s+)?INDEX IF NOT EXISTS\s+([a-zA-Z0-9_]+)", re.IGNORECASE)


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: file not found: {path}")
    return path.read_text(encoding="utf-8")


def _extract_set(pattern: re.Pattern[str], text: str) -> set[str]:
    return {name.strip() for name in pattern.findall(text)}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    schema_text = _read(root / "schema.sql")
    runtime_text = _read(root / "src/database.py")

    schema_tables = _extract_set(TABLE_RE, schema_text)
    runtime_tables = _extract_set(TABLE_RE, runtime_text)
    schema_indexes = _extract_set(INDEX_RE, schema_text)
    runtime_indexes = _extract_set(INDEX_RE, runtime_text)

    errors: list[str] = []

    only_runtime_tables = sorted(runtime_tables - schema_tables)
    only_schema_tables = sorted(schema_tables - runtime_tables)
    only_runtime_indexes = sorted(runtime_indexes - schema_indexes)
    only_schema_indexes = sorted(schema_indexes - runtime_indexes)

    if only_runtime_tables:
        errors.append(f"tables only in runtime: {only_runtime_tables}")
    if only_schema_tables:
        errors.append(f"tables only in schema: {only_schema_tables}")
    if only_runtime_indexes:
        errors.append(f"indexes only in runtime: {only_runtime_indexes}")
    if only_schema_indexes:
        errors.append(f"indexes only in schema: {only_schema_indexes}")

    if errors:
        raise SystemExit("ERROR: schema/runtime parity violation(s):\n- " + "\n- ".join(errors))

    print("OK: schema/runtime parity smoke passed.")


if __name__ == "__main__":
    main()
