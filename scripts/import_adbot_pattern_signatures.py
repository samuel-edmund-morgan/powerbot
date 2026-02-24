#!/usr/bin/env python3
"""
Import anonymized adbot matcher signatures/stats into sqlite.

Input JSON format:
{
  "signatures": [
    {
      "intent": "electrician_contact",
      "pattern": "номер електрика",
      "weight": 1.0,
      "lang": "uk",
      "confidence_floor": 120,
      "source_chat_tag": "newcastle_group",
      "stats_count": 10
    }
  ],
  "stats_daily": [
    {
      "date": "2026-02-24",
      "chat_tag": "newcastle_group",
      "intent": "electrician_contact",
      "matched": 15,
      "unmatched": 2,
      "fp_flags": 1
    }
  ]
}
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def _sanitize_text(value: object) -> str:
    return str(value or "").strip()


def _sanitize_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _sanitize_float(value: object, default: float = 1.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _load_payload(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {"signatures": payload, "stats_daily": []}
    if not isinstance(payload, dict):
        raise SystemExit("ERROR: input json must be object or signatures list")
    return payload


def _upsert_signatures(conn: sqlite3.Connection, signatures: list[dict]) -> int:
    count = 0
    for row in signatures:
        intent = _sanitize_text(row.get("intent"))
        pattern = _sanitize_text(row.get("pattern"))
        if not intent or not pattern:
            continue
        weight = _sanitize_float(row.get("weight"), 1.0)
        lang = _sanitize_text(row.get("lang")) or None
        confidence_floor = _sanitize_int(row.get("confidence_floor"), 0)
        source_chat_tag = _sanitize_text(row.get("source_chat_tag")) or None
        stats_count = max(_sanitize_int(row.get("stats_count"), 1), 0)
        conn.execute(
            """
            INSERT INTO adbot_pattern_signatures(
                intent, pattern, weight, lang, confidence_floor, source_chat_tag, stats_count, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(intent, pattern, source_chat_tag)
            DO UPDATE SET
                weight=excluded.weight,
                lang=excluded.lang,
                confidence_floor=excluded.confidence_floor,
                stats_count=adbot_pattern_signatures.stats_count + excluded.stats_count,
                updated_at=excluded.updated_at
            """,
            (intent, pattern, weight, lang, confidence_floor, source_chat_tag, stats_count),
        )
        count += 1
    return count


def _upsert_stats_daily(conn: sqlite3.Connection, rows: list[dict]) -> int:
    count = 0
    for row in rows:
        date = _sanitize_text(row.get("date"))
        chat_tag = _sanitize_text(row.get("chat_tag"))
        intent = _sanitize_text(row.get("intent"))
        if not date or not chat_tag or not intent:
            continue
        matched = max(_sanitize_int(row.get("matched"), 0), 0)
        unmatched = max(_sanitize_int(row.get("unmatched"), 0), 0)
        fp_flags = max(_sanitize_int(row.get("fp_flags"), 0), 0)
        conn.execute(
            """
            INSERT INTO adbot_pattern_stats_daily(
                date, chat_tag, intent, matched, unmatched, fp_flags, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(date, chat_tag, intent)
            DO UPDATE SET
                matched=adbot_pattern_stats_daily.matched + excluded.matched,
                unmatched=adbot_pattern_stats_daily.unmatched + excluded.unmatched,
                fp_flags=adbot_pattern_stats_daily.fp_flags + excluded.fp_flags,
                updated_at=excluded.updated_at
            """,
            (date, chat_tag, intent, matched, unmatched, fp_flags),
        )
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Import anonymized adbot signatures into sqlite")
    parser.add_argument("--db-path", required=True, help="Path to sqlite state.db")
    parser.add_argument("--input-json", required=True, help="Path to signatures json file")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    input_path = Path(args.input_json)
    if not db_path.exists():
        raise SystemExit(f"ERROR: db not found: {db_path}")
    if not input_path.exists():
        raise SystemExit(f"ERROR: input file not found: {input_path}")

    payload = _load_payload(input_path)
    signatures = payload.get("signatures") or []
    stats_daily = payload.get("stats_daily") or []
    if not isinstance(signatures, list) or not isinstance(stats_daily, list):
        raise SystemExit("ERROR: signatures/stats_daily must be lists")

    conn = sqlite3.connect(str(db_path))
    try:
        with conn:
            signatures_count = _upsert_signatures(conn, signatures)
            stats_count = _upsert_stats_daily(conn, stats_daily)
    finally:
        conn.close()

    print(
        "OK: imported adbot patterns "
        f"(signatures={signatures_count}, stats_daily={stats_count}) into {db_path}"
    )


if __name__ == "__main__":
    main()

