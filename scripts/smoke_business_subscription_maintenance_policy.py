#!/usr/bin/env python3
"""
Static smoke-check: business subscription maintenance guard policy.

Policy:
- lifecycle guard must be enabled when either BUSINESS_MODE or businessbot token is enabled.
- main runtime must schedule subscription_maintenance_loop via this guard.
- maintenance loop itself must use the same guard function.
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


GUARDS_FILE = _resolve("src/business/guards.py")
MAIN_FILE = _resolve("src/main.py")
MAINTENANCE_FILE = _resolve("src/business/maintenance.py")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _read(path: Path) -> str:
    _assert(path.exists(), f"file not found: {path}")
    return path.read_text(encoding="utf-8")


def _function_node(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function not found: {name}")


def _call_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                names.add(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                names.add(child.func.attr)
    return names


def _main_has_guarded_maintenance(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.Call) and isinstance(test.func, ast.Name)):
            continue
        if test.func.id != "is_business_subscription_lifecycle_enabled":
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
                continue
            call = stmt.value
            if not (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "create_task"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "asyncio"
            ):
                continue
            if not call.args:
                continue
            arg0 = call.args[0]
            if isinstance(arg0, ast.Call) and isinstance(arg0.func, ast.Name) and arg0.func.id == "subscription_maintenance_loop":
                return True
    return False


def _maintenance_uses_guard(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.UnaryOp) or not isinstance(test.op, ast.Not):
            continue
        operand = test.operand
        if not (isinstance(operand, ast.Call) and isinstance(operand.func, ast.Name)):
            continue
        if operand.func.id != "is_business_subscription_lifecycle_enabled":
            continue
        return True
    return False


def main() -> None:
    guards_tree = ast.parse(_read(GUARDS_FILE))
    main_tree = ast.parse(_read(MAIN_FILE))
    maintenance_tree = ast.parse(_read(MAINTENANCE_FILE))

    guard_fn = _function_node(guards_tree, "is_business_subscription_lifecycle_enabled")
    guard_calls = _call_names(guard_fn)
    violations: list[str] = []
    if "is_business_mode_enabled" not in guard_calls:
        violations.append("guard must include is_business_mode_enabled()")
    if "is_business_bot_enabled" not in guard_calls:
        violations.append("guard must include is_business_bot_enabled()")

    if not _main_has_guarded_maintenance(main_tree):
        violations.append("main.py must start subscription_maintenance_loop under is_business_subscription_lifecycle_enabled()")

    if not _maintenance_uses_guard(maintenance_tree):
        violations.append("maintenance loop must early-return when is_business_subscription_lifecycle_enabled() is false")

    if violations:
        raise SystemExit(
            "ERROR: business subscription maintenance policy violation(s):\n"
            + "\n".join(f"- {item}" for item in violations)
        )

    print("OK: business subscription maintenance policy smoke passed.")


if __name__ == "__main__":
    main()
