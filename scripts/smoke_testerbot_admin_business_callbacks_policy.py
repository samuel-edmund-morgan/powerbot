#!/usr/bin/env python3
"""
Static policy smoke:
testerbot admin read-only callback map must keep business coverage contract.

Checks:
- callbacks whitelist includes read-only business sections `abiz_payments`, `abiz_audit`
- callbacks strict whitelist does NOT include data-dependent `abiz_subs_export`
- admin scenario still contains read-only traversal for UI sections `Платежі` and `Аудит`
"""

from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from testerbot.callback_contract import READ_ONLY_INCLUDE_EQ


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    admin_scenario = (REPO_ROOT / "src" / "testerbot" / "scenarios" / "admin.py").read_text(encoding="utf-8")

    admin_eq = READ_ONLY_INCLUDE_EQ.get("admin") or set()
    _assert(admin_eq, "failed to read READ_ONLY_INCLUDE_EQ['admin'] from callback_contract.py")

    _assert("abiz_payments" in admin_eq, "callbacks whitelist must include `abiz_payments`")
    _assert("abiz_audit" in admin_eq, "callbacks whitelist must include `abiz_audit`")
    _assert("abiz_subs_export" not in admin_eq, "callbacks whitelist must NOT include `abiz_subs_export`")

    _assert('"Платежі"' in admin_scenario, "admin scenario must include `Платежі` read-only section traversal")
    _assert('"Аудит"' in admin_scenario, "admin scenario must include `Аудит` read-only section traversal")
    _assert(
        "open_business_section_from_main" in admin_scenario,
        "admin scenario must use deterministic business-section route helper",
    )

    print("OK: testerbot admin business callbacks policy smoke passed.")


if __name__ == "__main__":
    main()
