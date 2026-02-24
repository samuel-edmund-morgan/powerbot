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

import ast
from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _extract_read_only_include_eq(callbacks_py: str) -> set[str]:
    tree = ast.parse(callbacks_py)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_READ_ONLY_INCLUDE_EQ":
                    value = node.value
                    if isinstance(value, ast.Dict):
                        for k, v in zip(value.keys, value.values):
                            if isinstance(k, ast.Constant) and k.value == "admin":
                                if isinstance(v, ast.Set):
                                    out: set[str] = set()
                                    for elt in v.elts:
                                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                            out.add(elt.value)
                                    return out
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "_READ_ONLY_INCLUDE_EQ":
                value = node.value
                if isinstance(value, ast.Dict):
                    for k, v in zip(value.keys, value.values):
                        if isinstance(k, ast.Constant) and k.value == "admin":
                            if isinstance(v, ast.Set):
                                out: set[str] = set()
                                for elt in v.elts:
                                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                        out.add(elt.value)
                                return out
    return set()


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    callbacks_text = (repo_root / "src" / "testerbot" / "callbacks.py").read_text(encoding="utf-8")
    admin_scenario = (repo_root / "src" / "testerbot" / "scenarios" / "admin.py").read_text(encoding="utf-8")

    admin_eq = _extract_read_only_include_eq(callbacks_text)
    _assert(admin_eq, "failed to parse _READ_ONLY_INCLUDE_EQ['admin'] from callbacks.py")

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
