#!/usr/bin/env python3
"""
Static smoke-check: business payment pipeline policy.

Policy goals:
- UI-facing payment handlers (`mock`/`telegram_stars`) must route through
  `apply_payment_event(...)` instead of mutating subscription flags directly.
- `create_payment_intent(...)` must record `invoice_created` via repository event insert.
- `apply_payment_event(...)` is the canonical transition point and uses
  `_activate_paid_subscription(...)` for successful payments.

Run:
  python3 scripts/smoke_business_payment_pipeline_policy.py
"""

from __future__ import annotations

import ast
from pathlib import Path


def _resolve(path_rel: str) -> Path:
    candidates = []
    try:
        candidates.append(Path(__file__).resolve().parents[1] / path_rel)
    except Exception:
        pass
    candidates.extend([Path.cwd() / path_rel, Path("/app") / path_rel])
    for p in candidates:
        if p.exists():
            return p
    return candidates[0] if candidates else Path(path_rel)


SERVICE_FILE = _resolve("src/business/service.py")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _read(path: Path) -> str:
    _assert(path.exists(), f"file not found: {path}")
    return path.read_text(encoding="utf-8")


def _method_node(tree: ast.AST, class_name: str, method_name: str) -> ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.AsyncFunctionDef) and child.name == method_name:
                    return child
    raise AssertionError(f"method not found: {class_name}.{method_name}")


def _self_call_names(method: ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(method):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id == "self":
                names.add(node.func.attr)
    return names


def _self_repo_call_names(method: ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(method):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if (
                isinstance(owner, ast.Attribute)
                and owner.attr == "repository"
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "self"
            ):
                names.add(node.func.attr)
    return names


def _contains_string_constant(method: ast.AsyncFunctionDef, value: str) -> bool:
    for node in ast.walk(method):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == value:
                return True
    return False


def main() -> None:
    tree = ast.parse(_read(SERVICE_FILE))
    violations: list[str] = []

    apply_payment_event = _method_node(tree, "BusinessCabinetService", "apply_payment_event")
    create_payment_intent = _method_node(tree, "BusinessCabinetService", "create_payment_intent")
    apply_mock_payment_result = _method_node(tree, "BusinessCabinetService", "apply_mock_payment_result")
    validate_pre_checkout = _method_node(tree, "BusinessCabinetService", "validate_telegram_stars_pre_checkout")
    apply_telegram_success = _method_node(tree, "BusinessCabinetService", "apply_telegram_stars_successful_payment")

    apply_calls = _self_call_names(apply_payment_event)
    if "_activate_paid_subscription" not in apply_calls:
        violations.append("apply_payment_event must call _activate_paid_subscription for success transitions")

    intent_repo_calls = _self_repo_call_names(create_payment_intent)
    if "create_payment_event" not in intent_repo_calls:
        violations.append("create_payment_intent must persist invoice_created via repository.create_payment_event")
    if not _contains_string_constant(create_payment_intent, "invoice_created"):
        violations.append("create_payment_intent must emit invoice_created event type")

    for method_name, method_node in [
        ("apply_mock_payment_result", apply_mock_payment_result),
        ("validate_telegram_stars_pre_checkout", validate_pre_checkout),
        ("apply_telegram_stars_successful_payment", apply_telegram_success),
    ]:
        calls = _self_call_names(method_node)
        if "apply_payment_event" not in calls:
            violations.append(f"{method_name} must route through apply_payment_event")

        repo_calls = _self_repo_call_names(method_node)
        forbidden_repo_calls = {
            "update_subscription",
            "update_place_business_flags",
            "write_audit_log",
        }.intersection(repo_calls)
        if forbidden_repo_calls:
            violations.append(
                f"{method_name} must not mutate subscription/place directly: {sorted(forbidden_repo_calls)}"
            )

        if "_activate_paid_subscription" in calls:
            violations.append(f"{method_name} must not call _activate_paid_subscription directly")

    if violations:
        raise SystemExit(
            "ERROR: business payment pipeline policy violation(s):\n"
            + "\n".join(f"- {v}" for v in violations)
        )

    print("OK: business payment pipeline policy smoke passed.")


if __name__ == "__main__":
    main()
