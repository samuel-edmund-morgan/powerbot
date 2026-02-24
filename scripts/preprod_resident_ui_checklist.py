#!/usr/bin/env python3
"""
Pre-prod checklist for resident UI stealth readiness.

Outputs:
- git revision;
- hashes of key resident UI files;
- verified published places count;
- monetization stealth smoke result for zero-verified state.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _git_short_rev() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out or "unknown"
    except Exception:
        return "unknown"


def _count_verified(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM places
            WHERE COALESCE(is_published, 1) = 1
              AND COALESCE(is_verified, 0) = 1
            """
        ).fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def _run_stealth_smoke() -> tuple[bool, str]:
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "smoke_no_monetization_when_no_verified.py")]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    if proc.returncode == 0:
        return True, proc.stdout.strip() or "OK"
    output = (proc.stdout + "\n" + proc.stderr).strip()
    return False, output or "failed"


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-prod resident UI stealth checklist")
    parser.add_argument(
        "--db-path",
        default="/opt/powerbot-test/state.db",
        help="Path to sqlite state.db for verified_count check",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        raise SystemExit(f"ERROR: db path not found: {db_path}")

    handlers_file = REPO_ROOT / "src" / "handlers.py"
    business_file = REPO_ROOT / "src" / "business" / "service.py"
    revision = _git_short_rev()
    verified_count = _count_verified(db_path)

    print("Pre-prod resident UI checklist")
    print(f"- revision: {revision}")
    print(f"- handlers_sha256: {_sha256(handlers_file)}")
    print(f"- business_service_sha256: {_sha256(business_file)}")
    print(f"- verified_count: {verified_count}")

    if verified_count == 0:
        ok, details = _run_stealth_smoke()
        print(f"- stealth_smoke: {'PASS' if ok else 'FAIL'}")
        if details:
            print(f"- stealth_smoke_details: {details}")
        if not ok:
            raise SystemExit("ERROR: stealth smoke failed for verified_count=0")
    else:
        print("- stealth_smoke: SKIPPED (verified_count > 0)")

    print("OK: pre-prod resident UI checklist passed.")


if __name__ == "__main__":
    main()

