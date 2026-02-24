#!/usr/bin/env python3
"""
Static policy smoke:
testerbot admin scenario must keep claim-token open flow read-only-safe.

Goal:
- ensure `abiz_tokv_o|` callback is exercised only behind an allowlist of
  places that already have active claim tokens (no implicit token creation).
- prevent accidental removal of dead-end/pagination safety around claim-token UI.
"""

from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from testerbot.callback_contract import READ_ONLY_INCLUDE_SW


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    admin_scenario = (REPO_ROOT / "src" / "testerbot" / "scenarios" / "admin.py").read_text(
        encoding="utf-8"
    )

    required_tokens = (
        "_load_places_with_active_claim_token",
        "active_claim_token_place_ids",
        "SELECT DISTINCT place_id",
        "FROM business_claim_tokens",
        "status = 'active' AND expires_at > ?",
        'click_callback_prefix_and_wait(',
        '"abiz_tokv_o|"',
        "place_id_allowlist=active_claim_token_place_ids",
        "admin business tokens place open",
        "assert_not_dead_end(msg, ctx_name=\"admin business tokens services open\")",
        "assert_not_dead_end(msg, ctx_name=\"admin business tokens places open\")",
    )

    for token in required_tokens:
        _assert(
            token in admin_scenario,
            f"admin testerbot claim-token readonly policy token is missing: {token}",
        )

    admin_sw = READ_ONLY_INCLUDE_SW.get("admin") or set()
    _assert(admin_sw, "failed to read READ_ONLY_INCLUDE_SW['admin'] from callback_contract.py")
    _assert("abiz_tokv_o|" in admin_sw, "callbacks read-only whitelist must include admin prefix `abiz_tokv_o|`")

    print("OK: testerbot admin claim-token readonly policy smoke passed.")


if __name__ == "__main__":
    main()
