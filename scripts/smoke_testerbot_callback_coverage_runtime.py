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
    parser.add_argument(
        "--min-resident-clicked",
        type=int,
        default=20,
        help="Minimum resident clicked callbacks threshold",
    )
    parser.add_argument(
        "--min-business-clicked",
        type=int,
        default=10,
        help="Minimum business clicked callbacks threshold",
    )
    args = parser.parse_args()

    path = Path(args.coverage_file)
    _assert(path.exists(), f"coverage report file not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    bots = payload.get("bots") or {}
    _assert(isinstance(bots, dict), "coverage report: `bots` must be object")
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

    resident = bots.get("resident") or {}
    admin = bots.get("admin") or {}
    business = bots.get("business") or {}
    _assert(isinstance(resident, dict), "coverage report: missing resident section")
    _assert(isinstance(admin, dict), "coverage report: missing admin section")
    _assert(isinstance(business, dict), "coverage report: missing business section")

    resident_clicked, resident_seen, resident_clicked_count = _validate_bot_no_missing(resident, "resident")
    admin_clicked, admin_seen, admin_clicked_count = _validate_bot_no_missing(admin, "admin")
    business_clicked, business_seen, business_clicked_count = _validate_bot_no_missing(business, "business")

    # Resident baseline
    _assert("search_menu" in resident_clicked, "resident clicked must include `search_menu`")
    _assert("places_menu" in resident_clicked, "resident clicked must include `places_menu`")
    _assert("utilities_menu" in resident_clicked, "resident clicked must include `utilities_menu`")
    _assert("notifications_menu" in resident_clicked, "resident clicked must include `notifications_menu`")
    _assert(_has_prefix(resident_clicked, "places_cat_"), "resident clicked must include `places_cat_*`")
    _assert(_has_prefix(resident_clicked, "place_"), "resident clicked must include `place_*`")
    _assert(_has_prefix(resident_seen, "plrep_"), "resident seen must include `plrep_*`")
    _assert(
        resident_clicked_count >= int(args.min_resident_clicked),
        f"resident clicked callbacks below floor: {resident_clicked_count} < {args.min_resident_clicked}",
    )

    # Admin baseline
    _assert("admin_jobs_export" in admin_clicked, "admin clicked must include `admin_jobs_export`")
    _assert(_has_prefix(admin_clicked, "admin_sensor|"), "admin clicked must include `admin_sensor|*`")
    _assert(_has_prefix(admin_clicked, "abiz_tokv_s|"), "admin clicked must include `abiz_tokv_s|*`")
    _assert(_has_prefix(admin_clicked, "abiz_tokv_o|"), "admin clicked must include `abiz_tokv_o|*`")
    _assert("abiz_payments" in admin_seen, "admin seen must include `abiz_payments`")
    _assert("abiz_audit" in admin_seen, "admin seen must include `abiz_audit`")
    _assert(
        admin_clicked_count >= int(args.min_admin_clicked),
        f"admin clicked callbacks below floor: {admin_clicked_count} < {args.min_admin_clicked}",
    )

    # Business baseline
    _assert("bmenu:mine" in business_clicked, "business clicked must include `bmenu:mine`")
    _assert("bmenu:plans" in business_clicked, "business clicked must include `bmenu:plans`")
    _assert("bmenu:add" in business_clicked, "business clicked must include `bmenu:add`")
    _assert("bmenu:attach" in business_clicked, "business clicked must include `bmenu:attach`")
    _assert("bmenu:cancel" in business_clicked, "business clicked must include `bmenu:cancel`")
    _assert(_has_prefix(business_clicked, "bp_menu:"), "business clicked must include `bp_menu:*`")
    _assert(
        _has_prefix(business_seen, "bmy_o:") or _has_prefix(business_clicked, "bmy_o:"),
        "business coverage must include owner-card callbacks (`bmy_o:*`)",
    )
    _assert(
        business_clicked_count >= int(args.min_business_clicked),
        f"business clicked callbacks below floor: {business_clicked_count} < {args.min_business_clicked}",
    )

    print("OK: testerbot callback coverage runtime smoke passed.")


if __name__ == "__main__":
    main()
