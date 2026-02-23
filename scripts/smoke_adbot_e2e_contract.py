#!/usr/bin/env python3
"""
Static contract smoke for adbot real-E2E runner.

Goals:
- keep required source-chat scenarios from AGENTS backlog in the runner;
- keep anti-false-positive checks (no source reply + no internal audit);
- avoid regressions even when real E2E is optional/disabled in CI.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _collect_scenario_prompts(tree: ast.AST) -> set[str]:
    prompts: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "Scenario":
            continue
        for kw in node.keywords:
            if kw.arg != "prompt":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                prompts.add(kw.value.value.strip().casefold())
    return prompts


def _collect_called_function_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "e2e_adbot_test_groups.py"
    source = script_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(script_path))

    prompts = _collect_scenario_prompts(tree)
    required_prompt_prefixes = (
        "дайте номер електрика",
        "чи є світло в ньюкасл",
        "де оформити перепустку в паркінг",
    )

    for prompt_prefix in required_prompt_prefixes:
        _assert(
            any(prompt.startswith(prompt_prefix) for prompt in prompts),
            f"Missing required adbot E2E scenario prompt prefix: {prompt_prefix!r}",
        )

    called = _collect_called_function_names(tree)
    _assert(
        "_assert_no_source_reply" in called,
        "Anti-false-positive source-chat guard is missing in e2e_adbot_test_groups.py",
    )
    _assert(
        "_assert_no_internal_audit_for_prompt" in called,
        "Anti-false-positive internal-audit guard is missing in e2e_adbot_test_groups.py",
    )

    print("OK: adbot E2E contract smoke passed.")


if __name__ == "__main__":
    main()
