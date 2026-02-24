#!/usr/bin/env python3
"""
Runtime smoke for testerbot callback coverage report.

Validates admin read-only callback runtime coverage against deterministic baseline:
- admin coverage report exists and has no missing callbacks in strict mode;
- clicked callbacks contain key read-only paths (`admin_jobs_export`,
  `admin_sensor|*`, `abiz_tokv_s|*`, `abiz_tokv_o|*`);
- seen callbacks contain business read-only sections (`abiz_payments`, `abiz_audit`);
- admin clicked count is not below configured floor.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _has_prefix(values: list[str], prefix: str) -> bool:
    return any(str(v).startswith(prefix) for v in values)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate testerbot callback coverage runtime report")
    parser.add_argument(
        "--coverage-file",
        default="/opt/powerbot-test/logs/testerbot_callback_coverage.json",
        help="Path to testerbot coverage json",
    )
    parser.add_argument(
        "--min-admin-clicked",
        type=int,
        default=18,
        help="Minimum admin clicked callbacks threshold",
    )
    args = parser.parse_args()

    path = Path(args.coverage_file)
    _assert(path.exists(), f"coverage report file not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    bots = payload.get("bots") or {}
    _assert(isinstance(bots, dict), "coverage report: `bots` must be object")
    admin = bots.get("admin") or {}
    _assert(isinstance(admin, dict), "coverage report: missing admin section")

    missing = admin.get("missing") or {}
    missing_total = (
        len(missing.get("eq") or [])
        + len(missing.get("startswith") or [])
        + len(missing.get("regexp") or [])
    )
    _assert(missing_total == 0, f"admin coverage has missing callbacks: {missing}")

    clicked = [str(v) for v in (admin.get("clicked") or [])]
    seen = [str(v) for v in (admin.get("seen") or [])]
    stats = admin.get("stats") or {}
    clicked_count = int(stats.get("clicked") or len(clicked))

    _assert("admin_jobs_export" in clicked, "admin clicked must include `admin_jobs_export`")
    _assert(_has_prefix(clicked, "admin_sensor|"), "admin clicked must include `admin_sensor|*`")
    _assert(_has_prefix(clicked, "abiz_tokv_s|"), "admin clicked must include `abiz_tokv_s|*`")
    _assert(_has_prefix(clicked, "abiz_tokv_o|"), "admin clicked must include `abiz_tokv_o|*`")
    _assert("abiz_payments" in seen, "admin seen must include `abiz_payments`")
    _assert("abiz_audit" in seen, "admin seen must include `abiz_audit`")
    _assert(
        clicked_count >= int(args.min_admin_clicked),
        f"admin clicked callbacks below floor: {clicked_count} < {args.min_admin_clicked}",
    )

    print("OK: testerbot callback coverage runtime smoke passed.")


if __name__ == "__main__":
    main()

