#!/usr/bin/env python3
"""
Static policy smoke:
central callback contract for testerbot must stay consistent.

Checks:
- centralized contract module exists and defines runtime requirements for
  resident/admin/business;
- read-only include contract callback rules exist in real handler inventory;
- runtime requirement callbacks/prefixes are not empty and are coherent.
"""

from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from testerbot.callback_contract import (
    READ_ONLY_INCLUDE_EQ,
    READ_ONLY_INCLUDE_RG,
    READ_ONLY_INCLUDE_SW,
    RUNTIME_COVERAGE_REQUIREMENTS,
)
from testerbot.callbacks import parse_callback_inventory


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _ensure_subset(name: str, subset: set[str], superset: set[str], kind: str) -> None:
    missing = sorted(subset - superset)
    _assert(not missing, f"{name}: missing {kind} callbacks in handlers inventory: {missing}")


def main() -> None:
    repo_root = REPO_ROOT
    inventory = parse_callback_inventory(repo_root)

    for bot in ("resident", "admin", "business"):
        _assert(bot in RUNTIME_COVERAGE_REQUIREMENTS, f"runtime contract missing bot `{bot}`")
        _assert(bot in inventory, f"callback inventory missing bot `{bot}`")

    # Contract include rules must reference real callback patterns from handlers.
    for bot, include_eq in READ_ONLY_INCLUDE_EQ.items():
        _ensure_subset(bot, set(include_eq), set(inventory[bot]["eq"]), "eq")
    for bot, include_sw in READ_ONLY_INCLUDE_SW.items():
        _ensure_subset(bot, set(include_sw), set(inventory[bot]["startswith"]), "startswith")
    for bot, include_rg in READ_ONLY_INCLUDE_RG.items():
        _ensure_subset(bot, set(include_rg), set(inventory[bot]["regexp"]), "regexp")

    # Runtime requirements must be non-empty and structurally coherent.
    for bot, req in RUNTIME_COVERAGE_REQUIREMENTS.items():
        min_clicked = int(req.get("min_clicked") or 0)
        _assert(min_clicked > 0, f"{bot}: min_clicked must be > 0")

        clicked_eq = set(req.get("clicked_eq") or set())
        clicked_prefix = set(req.get("clicked_prefix") or set())
        seen_eq = set(req.get("seen_eq") or set())
        seen_prefix = set(req.get("seen_prefix") or set())
        _assert(
            clicked_eq or clicked_prefix or seen_eq or seen_prefix,
            f"{bot}: runtime requirement must define at least one callback signal",
        )

    print("OK: testerbot callback contract policy smoke passed.")


if __name__ == "__main__":
    main()
