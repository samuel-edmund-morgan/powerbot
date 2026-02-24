#!/usr/bin/env python3
"""Runtime smoke for test-only business Stars price overrides."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    plans_path = SRC_DIR / "business" / "plans.py"
    spec = importlib.util.spec_from_file_location("business_plans_runtime_smoke", plans_path)
    _assert(spec and spec.loader, f"failed to load module spec: {plans_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    PLAN_STARS_PRICES = getattr(module, "PLAN_STARS_PRICES")
    get_effective_plan_stars_prices = getattr(module, "get_effective_plan_stars_prices")
    get_plan_stars_price = getattr(module, "get_plan_stars_price")

    keys = (
        "BUSINESS_TEST_STARS_PRICE_LIGHT",
        "BUSINESS_TEST_STARS_PRICE_PRO",
        "BUSINESS_TEST_STARS_PRICE_PARTNER",
    )
    backup = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        defaults = get_effective_plan_stars_prices()
        _assert(defaults == PLAN_STARS_PRICES, f"default prices mismatch: {defaults} != {PLAN_STARS_PRICES}")

        os.environ["BUSINESS_TEST_STARS_PRICE_LIGHT"] = "1"
        os.environ["BUSINESS_TEST_STARS_PRICE_PRO"] = "2"
        os.environ["BUSINESS_TEST_STARS_PRICE_PARTNER"] = "3"
        overridden = get_effective_plan_stars_prices()
        _assert(overridden.get("light") == 1, f"light override failed: {overridden}")
        _assert(overridden.get("pro") == 2, f"pro override failed: {overridden}")
        _assert(overridden.get("partner") == 3, f"partner override failed: {overridden}")
        _assert(get_plan_stars_price("light") == 1, "get_plan_stars_price(light) should use override")
        _assert(get_plan_stars_price("pro") == 2, "get_plan_stars_price(pro) should use override")
        _assert(get_plan_stars_price("partner") == 3, "get_plan_stars_price(partner) should use override")

        # Invalid/negative values must be ignored.
        os.environ["BUSINESS_TEST_STARS_PRICE_LIGHT"] = "-1"
        os.environ["BUSINESS_TEST_STARS_PRICE_PRO"] = "abc"
        ignored = get_effective_plan_stars_prices()
        _assert(
            ignored.get("light") == PLAN_STARS_PRICES["light"],
            f"negative override must be ignored for light: {ignored}",
        )
        _assert(
            ignored.get("pro") == PLAN_STARS_PRICES["pro"],
            f"non-int override must be ignored for pro: {ignored}",
        )
        _assert(ignored.get("partner") == 3, f"valid partner override must stay applied: {ignored}")
    finally:
        for key, value in backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print("OK: business plan price overrides runtime smoke passed.")


if __name__ == "__main__":
    main()
