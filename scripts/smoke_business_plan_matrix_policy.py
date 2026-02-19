#!/usr/bin/env python3
"""Smoke-check: shared business plan matrix contract (static AST/text)."""

from __future__ import annotations

import ast
from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _literal_from_assignments(file_path: Path, name: str) -> object:
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"Missing assignment for {name} in {file_path}")


def main() -> None:
    # deploy_test runs from /opt/powerbot-test; resolve project root by script location.
    repo_root = Path(__file__).resolve().parents[1]
    plans_file = repo_root / "src" / "business" / "plans.py"
    handlers_file = repo_root / "src" / "business" / "handlers.py"
    service_file = repo_root / "src" / "business" / "service.py"
    admin_handlers_file = repo_root / "src" / "admin" / "handlers.py"

    plan_titles = _literal_from_assignments(plans_file, "PLAN_TITLES")
    plan_prices = _literal_from_assignments(plans_file, "PLAN_STARS_PRICES")
    paid_tiers = _literal_from_assignments(plans_file, "PAID_TIERS")
    supported_tiers = _literal_from_assignments(plans_file, "SUPPORTED_TIERS")

    expected_titles = {
        "free": "Free",
        "light": "Light",
        "pro": "Premium",
        "partner": "Partner",
    }
    expected_prices = {
        "light": 1000,
        "pro": 2500,
        "partner": 5000,
    }
    expected_paid = {"light", "pro", "partner"}
    expected_supported = {"free", "light", "pro", "partner"}

    _assert(dict(plan_titles) == expected_titles, f"Unexpected PLAN_TITLES: {plan_titles}")
    _assert(dict(plan_prices) == expected_prices, f"Unexpected PLAN_STARS_PRICES: {plan_prices}")
    _assert(set(paid_tiers) == expected_paid, f"Unexpected PAID_TIERS: {paid_tiers}")
    _assert(set(supported_tiers) == expected_supported, f"Unexpected SUPPORTED_TIERS: {supported_tiers}")
    _assert(dict(plan_titles).get("pro") == "Premium", "DB tier `pro` must be shown as `Premium` in UI.")

    handlers_text = handlers_file.read_text(encoding="utf-8")
    service_text = service_file.read_text(encoding="utf-8")
    admin_handlers_text = admin_handlers_file.read_text(encoding="utf-8")

    _assert("PLAN_STARS =" not in handlers_text, "business.handlers must not define local PLAN_STARS map.")
    _assert(
        "PLAN_STARS_PRICES: dict" not in service_text,
        "business.service must not define local PLAN_STARS_PRICES map.",
    )
    _assert(
        "from business.plans import PLAN_TITLES" in admin_handlers_text,
        "admin.handlers must import shared PLAN_TITLES from business.plans.",
    )
    _assert(
        "if tier == \"pro\":" not in admin_handlers_text,
        "admin.handlers should not hardcode tier title mapping (use shared PLAN_TITLES).",
    )

    print("OK: business plan matrix policy smoke passed.")


if __name__ == "__main__":
    main()
