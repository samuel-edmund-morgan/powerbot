#!/usr/bin/env python3
"""
Bootstrap deterministic claim-token precondition for testerbot admin read-only flow.

Why:
- testerbot admin scenario now covers `abiz_tokv_o|` (open claim token card),
  but this callback is data-dependent: it is available only for places listed in
  claim-token list.
- to keep `deploy_test` deterministic, we ensure at least one active token exists
  for the first place in the first service (same ordering as admin UI lists).
"""

from __future__ import annotations

import argparse
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_claim_token_precondition(db_path: Path) -> tuple[bool, str]:
    if not db_path.exists():
        return False, f"skip: db not found: {db_path}"

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {"general_services", "places", "business_claim_tokens"}
        if not required.issubset(tables):
            return False, f"skip: required tables missing: {sorted(required - tables)}"

        service_row = conn.execute(
            """
            SELECT s.id
            FROM general_services s
            JOIN places p ON p.service_id = s.id
            GROUP BY s.id, s.name
            ORDER BY s.name COLLATE NOCASE, s.id
            LIMIT 1
            """
        ).fetchone()
        if service_row is None:
            return False, "skip: no services with places"
        service_id = int(service_row["id"])

        place_row = conn.execute(
            """
            SELECT id
            FROM places
            WHERE service_id = ?
            ORDER BY name COLLATE NOCASE, id
            LIMIT 1
            """,
            (service_id,),
        ).fetchone()
        if place_row is None:
            return False, f"skip: no places in service_id={service_id}"
        place_id = int(place_row["id"])

        now_iso = _utc_now_iso()
        active_row = conn.execute(
            """
            SELECT id, token, expires_at
            FROM business_claim_tokens
            WHERE place_id = ?
              AND status = 'active'
              AND expires_at > ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (place_id, now_iso),
        ).fetchone()
        if active_row is not None:
            return (
                False,
                f"ok: active token already exists for place_id={place_id} token={active_row['token']}",
            )

        token = f"tb-{secrets.token_urlsafe(16).replace('-', 'A').replace('_', 'B')}"
        created_at = now_iso
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        conn.execute(
            """
            INSERT INTO business_claim_tokens(
                place_id,
                token,
                status,
                attempts_left,
                created_at,
                expires_at,
                created_by,
                used_at,
                used_by
            ) VALUES (?, ?, 'active', 5, ?, ?, NULL, NULL, NULL)
            """,
            (place_id, token, created_at, expires_at),
        )
        conn.commit()
        return True, f"ok: seeded active token for place_id={place_id} token={token}"
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap testerbot claim-token precondition")
    parser.add_argument(
        "--db-path",
        default="/opt/powerbot-test/state.db",
        help="Path to SQLite DB (default: /opt/powerbot-test/state.db)",
    )
    args = parser.parse_args()

    changed, message = _ensure_claim_token_precondition(Path(args.db_path))
    prefix = "CHANGED" if changed else "OK"
    print(f"{prefix}: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

