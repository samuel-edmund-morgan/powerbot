#!/usr/bin/env python3
"""
Runtime smoke for testerbot callback coverage report.

Validates read-only callback runtime coverage against deterministic baseline:
- resident/admin/business coverage sections exist and have no missing callbacks;
- clicked callbacks contain key read-only paths for each bot;
- seen callbacks contain key read-only screens for each bot;
- clicked counts are not below configured floors.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from testerbot.callback_contract import RUNTIME_COVERAGE_REQUIREMENTS


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _has_prefix(values: list[str], prefix: str) -> bool:
    return any(str(v).startswith(prefix) for v in values)


def _validate_bot_no_missing(bot_payload: dict, bot_name: str) -> tuple[list[str], list[str], int]:
    missing = bot_payload.get("missing") or {}
    missing_total = (
        len(missing.get("eq") or [])
        + len(missing.get("startswith") or [])
        + len(missing.get("regexp") or [])
    )
    _assert(missing_total == 0, f"{bot_name} coverage has missing callbacks: {missing}")
    clicked_local = [str(v) for v in (bot_payload.get("clicked") or [])]
    seen_local = [str(v) for v in (bot_payload.get("seen") or [])]
    stats_local = bot_payload.get("stats") or {}
    clicked_count_local = int(stats_local.get("clicked") or len(clicked_local))
    return clicked_local, seen_local, clicked_count_local


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
        default=int(RUNTIME_COVERAGE_REQUIREMENTS["admin"]["min_clicked"]),
        help="Minimum admin clicked callbacks threshold",
    )
    parser.add_argument(
        "--min-resident-clicked",
        type=int,
        default=int(RUNTIME_COVERAGE_REQUIREMENTS["resident"]["min_clicked"]),
        help="Minimum resident clicked callbacks threshold",
    )
    parser.add_argument(
        "--min-business-clicked",
        type=int,
        default=int(RUNTIME_COVERAGE_REQUIREMENTS["business"]["min_clicked"]),
        help="Minimum business clicked callbacks threshold",
    )
    args = parser.parse_args()

    path = Path(args.coverage_file)
    _assert(path.exists(), f"coverage report file not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    bots = payload.get("bots") or {}
    _assert(isinstance(bots, dict), "coverage report: `bots` must be object")
    resident = bots.get("resident") or {}
    admin = bots.get("admin") or {}
    business = bots.get("business") or {}
    _assert(isinstance(resident, dict), "coverage report: missing resident section")
    _assert(isinstance(admin, dict), "coverage report: missing admin section")
    _assert(isinstance(business, dict), "coverage report: missing business section")

    resident_clicked, resident_seen, resident_clicked_count = _validate_bot_no_missing(resident, "resident")
    admin_clicked, admin_seen, admin_clicked_count = _validate_bot_no_missing(admin, "admin")
    business_clicked, business_seen, business_clicked_count = _validate_bot_no_missing(business, "business")

    min_clicked_by_bot = {
        "resident": int(args.min_resident_clicked),
        "admin": int(args.min_admin_clicked),
        "business": int(args.min_business_clicked),
    }
    clicked_by_bot = {
        "resident": resident_clicked,
        "admin": admin_clicked,
        "business": business_clicked,
    }
    seen_by_bot = {
        "resident": resident_seen,
        "admin": admin_seen,
        "business": business_seen,
    }
    clicked_counts_by_bot = {
        "resident": resident_clicked_count,
        "admin": admin_clicked_count,
        "business": business_clicked_count,
    }

    for bot_name, req in RUNTIME_COVERAGE_REQUIREMENTS.items():
        clicked = clicked_by_bot[bot_name]
        seen = seen_by_bot[bot_name]
        for value in sorted(req.get("clicked_eq") or set()):
            _assert(value in clicked, f"{bot_name} clicked must include `{value}`")
        for value in sorted(req.get("clicked_prefix") or set()):
            _assert(_has_prefix(clicked, value), f"{bot_name} clicked must include `{value}*`")
        for value in sorted(req.get("seen_eq") or set()):
            _assert(value in seen, f"{bot_name} seen must include `{value}`")
        for value in sorted(req.get("seen_prefix") or set()):
            _assert(_has_prefix(seen, value), f"{bot_name} seen must include `{value}*`")

        floor = min_clicked_by_bot[bot_name]
        clicked_count = clicked_counts_by_bot[bot_name]
        _assert(
            clicked_count >= floor,
            f"{bot_name} clicked callbacks below floor: {clicked_count} < {floor}",
        )

    print("OK: testerbot callback coverage runtime smoke passed.")


if __name__ == "__main__":
    main()
