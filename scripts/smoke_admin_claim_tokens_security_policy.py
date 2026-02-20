#!/usr/bin/env python3
"""
Static smoke-check for admin claim-token security hygiene.

Policy:
- claim tokens in admin UI must be rendered in `<code>...</code>` blocks
- logger calls must not include token payload variables (token/token_row/rotated)
  as interpolation args/objects

Run:
  python3 scripts/smoke_admin_claim_tokens_security_policy.py
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


def _resolve_handlers_path() -> Path:
    candidates = []
    try:
        candidates.append(Path(__file__).resolve().parents[1] / "src/admin/handlers.py")
    except Exception:
        pass
    candidates.extend(
        [
            Path.cwd() / "src/admin/handlers.py",
            Path("/app/src/admin/handlers.py"),
        ]
    )
    for path in candidates:
        if path.exists():
            return path
    return candidates[0] if candidates else Path("src/admin/handlers.py")


HANDLERS_PATH = _resolve_handlers_path()
SENSITIVE_NAMES = {"token", "token_row", "rotated"}


def _contains_token_payload(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in SENSITIVE_NAMES:
            return True
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "get":
            if sub.args and isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, str):
                key = sub.args[0].value.lower()
                if "token" in key:
                    return True
        if isinstance(sub, ast.Subscript):
            key_node = sub.slice
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                if "token" in key_node.value.lower():
                    return True
    return False


def main() -> None:
    if not HANDLERS_PATH.exists():
        raise SystemExit(f"ERROR: file not found: {HANDLERS_PATH}")

    source = HANDLERS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    violations: list[str] = []

    # UI contract: token must be shown in code-tag.
    code_tag_hits = len(re.findall(r"Token:\s*<code>\{token\}</code>", source))
    if code_tag_hits < 3:
        violations.append(
            "token UI must render `Token: <code>{token}</code>` in all admin claim-token screens "
            f"(found {code_tag_hits}, expected >= 3)"
        )
    for m in re.finditer(r"Token:", source):
        tail = source[m.start() : m.start() + 96]
        if "<code>{token}</code>" in tail:
            continue
        line = source.count("\n", 0, m.start()) + 1
        violations.append(f"line {line}: token label is not rendered inside <code>...</code>")

    # Logging contract: do not interpolate/pass token payload objects.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not isinstance(func.value, ast.Name) or func.value.id != "logger":
            continue
        if func.attr not in {"debug", "info", "warning", "error", "exception", "critical"}:
            continue

        # First arg is usually static template string; still guard f-strings there.
        if node.args and _contains_token_payload(node.args[0]):
            violations.append(
                f"line {node.lineno}: logger first argument contains token payload interpolation"
            )

        for arg in node.args[1:]:
            if _contains_token_payload(arg):
                violations.append(f"line {node.lineno}: logger args must not include token payload values")
        for kw in node.keywords:
            if kw.arg and _contains_token_payload(kw.value):
                violations.append(
                    f"line {node.lineno}: logger keyword `{kw.arg}` must not include token payload values"
                )

    if violations:
        msg = "\n".join(f"- {item}" for item in violations)
        raise SystemExit(f"ERROR: admin claim-token security policy violation(s):\n{msg}")

    print("OK: admin claim-token security policy smoke passed.")


if __name__ == "__main__":
    main()
