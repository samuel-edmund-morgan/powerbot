#!/usr/bin/env python3
"""
Static policy smoke:
all admin/business callback matchers must be explicitly classified as
read-only include or explicit exclude.

Goal:
- prevent silent callback drift when handlers gain new callback rules;
- keep strict coverage contract deterministic.
"""

from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from testerbot import callbacks as cb
from testerbot.callback_contract import (
    READ_ONLY_INCLUDE_EQ,
    READ_ONLY_INCLUDE_RG,
    READ_ONLY_INCLUDE_SW,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _unknown_rules(raw: set[str], include: set[str], exclude: set[str]) -> set[str]:
    return set(raw) - set(include) - set(exclude)


def _check_bot_partition(
    bot_name: str,
    inventory: dict[str, dict[str, set[str]]],
) -> None:
    raw_eq = set(inventory[bot_name]["eq"])
    raw_sw = set(inventory[bot_name]["startswith"])
    raw_rg = set(inventory[bot_name]["regexp"])

    include_eq = set(READ_ONLY_INCLUDE_EQ.get(bot_name, set()))
    include_sw = set(READ_ONLY_INCLUDE_SW.get(bot_name, set()))
    include_rg = set(READ_ONLY_INCLUDE_RG.get(bot_name, set()))

    exclude_eq = set(getattr(cb, "_READ_ONLY_EXCLUDE_EQ").get(bot_name, set()))
    exclude_sw = set(getattr(cb, "_READ_ONLY_EXCLUDE_SW").get(bot_name, set()))
    exclude_rg = set(getattr(cb, "_READ_ONLY_EXCLUDE_RG").get(bot_name, set()))

    _assert(
        not (include_eq & exclude_eq),
        f"{bot_name}: include/exclude eq overlap: {sorted(include_eq & exclude_eq)}",
    )
    _assert(
        not (include_sw & exclude_sw),
        f"{bot_name}: include/exclude startswith overlap: {sorted(include_sw & exclude_sw)}",
    )
    _assert(
        not (include_rg & exclude_rg),
        f"{bot_name}: include/exclude regexp overlap: {sorted(include_rg & exclude_rg)}",
    )

    unknown_eq = _unknown_rules(raw_eq, include_eq, exclude_eq)
    unknown_sw = _unknown_rules(raw_sw, include_sw, exclude_sw)
    unknown_rg = _unknown_rules(raw_rg, include_rg, exclude_rg)
    _assert(
        not unknown_eq,
        f"{bot_name}: unclassified eq callbacks (add to include or exclude): {sorted(unknown_eq)}",
    )
    _assert(
        not unknown_sw,
        f"{bot_name}: unclassified startswith callbacks (add to include or exclude): {sorted(unknown_sw)}",
    )
    _assert(
        not unknown_rg,
        f"{bot_name}: unclassified regexp callbacks (add to include or exclude): {sorted(unknown_rg)}",
    )


def main() -> None:
    inventory = cb.parse_callback_inventory(REPO_ROOT)
    for bot_name in ("admin", "business"):
        _check_bot_partition(bot_name, inventory)
    print("OK: testerbot callback partition policy smoke passed.")


if __name__ == "__main__":
    main()

