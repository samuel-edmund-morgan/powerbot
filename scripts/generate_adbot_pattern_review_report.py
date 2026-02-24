#!/usr/bin/env python3
"""
Generate markdown review report for anonymized adbot pattern signatures/stats.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def _query_rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(sql, params)
    return cur.fetchall()


def _render_md(signatures: list[sqlite3.Row], stats: list[sqlite3.Row]) -> str:
    lines: list[str] = [
        "# Adbot Pattern Review Report",
        "",
        "## Top Signatures",
        "",
        "| intent | pattern | chat_tag | weight | confidence_floor | stats_count |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in signatures:
        lines.append(
            "| {intent} | {pattern} | {tag} | {weight:.2f} | {floor} | {count} |".format(
                intent=str(row["intent"]),
                pattern=str(row["pattern"]),
                tag=str(row["source_chat_tag"] or "-"),
                weight=float(row["weight"] or 0.0),
                floor=int(row["confidence_floor"] or 0),
                count=int(row["stats_count"] or 0),
            )
        )

    lines.extend(
        [
            "",
            "## Daily Stats (latest days)",
            "",
            "| date | chat_tag | intent | matched | unmatched | fp_flags |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    for row in stats:
        lines.append(
            "| {date} | {chat_tag} | {intent} | {matched} | {unmatched} | {fp_flags} |".format(
                date=str(row["date"]),
                chat_tag=str(row["chat_tag"]),
                intent=str(row["intent"]),
                matched=int(row["matched"] or 0),
                unmatched=int(row["unmatched"] or 0),
                fp_flags=int(row["fp_flags"] or 0),
            )
        )

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate adbot pattern review markdown")
    parser.add_argument("--db-path", required=True, help="Path to sqlite state.db")
    parser.add_argument(
        "--output-md",
        default="/tmp/adbot_pattern_review.md",
        help="Output markdown path",
    )
    parser.add_argument("--limit", type=int, default=50, help="Rows per section")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        raise SystemExit(f"ERROR: db not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        signatures = _query_rows(
            conn,
            """
            SELECT intent, pattern, source_chat_tag, weight, confidence_floor, stats_count
            FROM adbot_pattern_signatures
            ORDER BY stats_count DESC, intent ASC, pattern ASC
            LIMIT ?
            """,
            (max(args.limit, 1),),
        )
        stats = _query_rows(
            conn,
            """
            SELECT date, chat_tag, intent, matched, unmatched, fp_flags
            FROM adbot_pattern_stats_daily
            ORDER BY date DESC, matched DESC
            LIMIT ?
            """,
            (max(args.limit, 1),),
        )
    finally:
        conn.close()

    md = _render_md(signatures, stats)
    output = Path(args.output_md)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(md, encoding="utf-8")
    print(f"OK: review report generated -> {output}")


if __name__ == "__main__":
    main()

