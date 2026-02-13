#!/usr/bin/env python3
"""
Static smoke-check for business bot user-facing copy hygiene.

Goal:
- Prevent accidental reintroduction of technical identifiers into owner UI texts
  (e.g. `place_id=...`, `request_id=...`) that degrade UX.

Scope:
- `src/business/handlers.py` string literals.
- Logging templates are explicitly allowed.

Run:
  python3 scripts/smoke_business_ui_copy_sanitized.py
"""

from __future__ import annotations

import ast
from pathlib import Path


def _resolve_handlers_path() -> Path:
    candidates = []
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


HANDLERS_PATH = _resolve_handlers_path()

FORBIDDEN_FRAGMENTS = (
    "place_id=",
    "request_id=",
    "owner_id=",
    "tg_user_id=",
    "external_payment_id=",
)


def _is_allowed_log_template(value: str) -> bool:
    # Keep logger templates untouched (they are not user-facing copy).
    return "Failed to " in value and "%s" in value


def main() -> None:
    if not HANDLERS_PATH.exists():
        raise SystemExit(f"ERROR: file not found: {HANDLERS_PATH}")

    source = HANDLERS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        value = node.value
        if _is_allowed_log_template(value):
            continue
        hit = next((fragment for fragment in FORBIDDEN_FRAGMENTS if fragment in value), None)
        if hit:
            preview = value.replace("\n", "\\n")
            if len(preview) > 140:
                preview = preview[:137] + "..."
            violations.append(f"line {node.lineno}: forbidden UI fragment `{hit}` in string: {preview!r}")

    if violations:
        raise SystemExit(
            "ERROR: business UI copy hygiene violation(s):\n"
            + "\n".join(f"- {item}" for item in violations)
        )

    print("OK: business UI copy hygiene smoke passed.")


if __name__ == "__main__":
    main()
