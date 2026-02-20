#!/usr/bin/env python3
"""
Static smoke-check: business callback handlers must not contain obvious dead code.

Policy:
- In `src/business/handlers.py`, callback handlers decorated with
  `@router.callback_query(...)` must not start with an unconditional top-level
  `return` followed by extra statements.

Why:
- This pattern silently turns handlers into no-op stubs and leaves unreachable
  logic in file, which breaks UX and hides regressions.

Run:
  python3 scripts/smoke_business_callback_deadcode_policy.py
"""

from __future__ import annotations

import ast
from pathlib import Path


def _resolve_handlers_path() -> Path:
    candidates: list[Path] = []
    try:
        candidates.append(Path(__file__).resolve().parents[1] / "src/business/handlers.py")
    except Exception:
        pass
    candidates.extend(
        [
            Path.cwd() / "src/business/handlers.py",
            Path("/app/src/business/handlers.py"),
        ]
    )
    for path in candidates:
        if path.exists():
            return path
    return candidates[0] if candidates else Path("src/business/handlers.py")


def _is_callback_handler(node: ast.AsyncFunctionDef) -> bool:
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if isinstance(func, ast.Attribute) and func.attr == "callback_query":
            return True
    return False


def main() -> None:
    handlers_path = _resolve_handlers_path()
    if not handlers_path.exists():
        raise SystemExit(f"ERROR: file not found: {handlers_path}")

    source = handlers_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    violations: list[str] = []

    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if not _is_callback_handler(node):
            continue
        if not node.body:
            continue
        first_stmt = node.body[0]
        if isinstance(first_stmt, ast.Return) and len(node.body) > 1:
            violations.append(
                f"{node.name} starts with unconditional return and has unreachable code after it"
            )

    if violations:
        details = "\n".join(f"- {item}" for item in violations)
        raise SystemExit(
            "ERROR: business callback dead-code policy violation(s):\n" + details
        )

    print("OK: business callback dead-code policy smoke passed.")


if __name__ == "__main__":
    main()

