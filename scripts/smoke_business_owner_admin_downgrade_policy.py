#!/usr/bin/env python3
"""
Static smoke-check: owner/admin downgrade responsibilities.

Policy:
- Owner flow must not allow direct paid->free downgrade while paid entitlement is active.
- Business plan UI must expose cancel-auto-renew action (`bp_cancel`) for paid owner flow.
- Admin flow must still support forced `Free` tier set via admin UI.
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
BUSINESS_HANDLERS_FILE = _resolve("src/business/handlers.py")
ADMIN_HANDLERS_FILE = _resolve("src/admin/handlers.py")
ADMIN_PROMO_SMOKE_FILE = _resolve("scripts/smoke_business_admin_subscription_promo.py")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _read(path: Path) -> str:
    _assert(path.exists(), f"file not found: {path}")
    return path.read_text(encoding="utf-8")


def _class_method_node(tree: ast.AST, class_name: str, method_name: str) -> ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.AsyncFunctionDef) and child.name == method_name:
                    return child
    raise AssertionError(f"method not found: {class_name}.{method_name}")


def _contains_string(node: ast.AST, value_substring: str) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if value_substring in child.value:
                return True
    return False


def _contains_call(node: ast.AST, func_name: str) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            fn = child.func
            if isinstance(fn, ast.Name) and fn.id == func_name:
                return True
            if isinstance(fn, ast.Attribute) and fn.attr == func_name:
                return True
    return False


def main() -> None:
    service_tree = ast.parse(_read(SERVICE_FILE))
    business_handlers = _read(BUSINESS_HANDLERS_FILE)
    admin_handlers = _read(ADMIN_HANDLERS_FILE)
    admin_promo_smoke = _read(ADMIN_PROMO_SMOKE_FILE)

    violations: list[str] = []

    change_tier = _class_method_node(service_tree, "BusinessCabinetService", "change_subscription_tier")
    if not _contains_call(change_tier, "_has_paid_entitlement"):
        violations.append("change_subscription_tier must guard owner paid->free using _has_paid_entitlement")
    if not _contains_string(change_tier, "Щоб перейти на Free, спочатку скасуй автопродовження"):
        violations.append("change_subscription_tier must raise validation for immediate owner free downgrade")

    if "bp_cancel:" not in business_handlers:
        violations.append("business owner plans UI must expose bp_cancel callback")
    if "🚫 Скасувати автопродовження" not in business_handlers:
        violations.append("business owner plans UI must render cancel-autorenew button text")

    if "CB_BIZ_PLACES_PROMO_SET_PREFIX}free|" not in admin_handlers:
        violations.append("admin promo menu must include Free option callback")
    if "admin_set_subscription_tier(" not in admin_handlers:
        violations.append("admin handlers must call business_service.admin_set_subscription_tier")

    if 'tier="free"' not in admin_promo_smoke:
        violations.append("admin subscription smoke must validate forced free tier path")

    if violations:
        raise SystemExit(
            "ERROR: owner/admin downgrade policy violation(s):\n"
            + "\n".join(f"- {item}" for item in violations)
        )

    print("OK: business owner/admin downgrade policy smoke passed.")


if __name__ == "__main__":
    main()
