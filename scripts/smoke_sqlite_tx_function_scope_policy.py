#!/usr/bin/env python3
"""
Static smoke-check: transaction scope policy at function level.

Policy:
- Raw SQL transactions (`BEGIN`/`BEGIN IMMEDIATE`) are allowed only in DB modules:
  - src/database.py
  - src/business/repository.py
- A function that opens a raw transaction must not contain Telegram/HTTP calls.

This keeps transaction windows DB-only and prevents long/fragile write scopes.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


def _repo_root() -> Path:
    try:
        p = Path(__file__).resolve().parents[1]
        if (p / "src").exists():
            return p
    except Exception:
        pass
    for c in (Path.cwd(), Path("/app")):
        if (c / "src").exists():
            return c
    raise RuntimeError("Cannot locate repo root with /src")


REPO_ROOT = _repo_root()
SRC_ROOT = REPO_ROOT / "src"

BEGIN_CALL_RE = re.compile(r"db\.execute\(\s*['\"]BEGIN(?:\s+IMMEDIATE)?['\"]", re.IGNORECASE)

# Keep markers explicit enough to avoid accidental false positives.
FORBIDDEN_NETWORK_MARKERS = (
    ".send_message(",
    ".edit_message_text(",
    ".answer(",
    ".answer_callback_query(",
    "Bot(",
    "aiohttp.ClientSession(",
    "requests.",
    "httpx.",
    "urllib.request",
)

ALLOWED_BEGIN_FILES = {
    Path("src/database.py"),
    Path("src/business/repository.py"),
}


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _iter_py_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if "__pycache__" not in p.parts
    )


def _function_source(text: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(text, node)
    return segment or ""


def main() -> None:
    violations: list[str] = []

    files = _iter_py_files(SRC_ROOT)
    _assert(files, f"No Python files found under {SRC_ROOT}")

    for file_path in files:
        rel_path = file_path.relative_to(REPO_ROOT)
        text = file_path.read_text(encoding="utf-8")

        try:
            tree = ast.parse(text, filename=str(file_path))
        except SyntaxError as exc:
            violations.append(f"{rel_path}: syntax error during smoke parse: {exc}")
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            fn_src = _function_source(text, node)
            if not fn_src:
                continue
            if not BEGIN_CALL_RE.search(fn_src):
                continue

            fn_name = node.name
            fn_location = f"{rel_path}:{getattr(node, 'lineno', '?')}:{fn_name}"

            if rel_path not in ALLOWED_BEGIN_FILES:
                violations.append(
                    f"{fn_location}: raw SQL BEGIN is allowed only in {sorted(str(p) for p in ALLOWED_BEGIN_FILES)}"
                )

            lower_fn_src = fn_src.lower()
            for marker in FORBIDDEN_NETWORK_MARKERS:
                if marker.lower() in lower_fn_src:
                    violations.append(
                        f"{fn_location}: forbidden network/Telegram marker `{marker}` inside transaction function"
                    )

    if violations:
        raise SystemExit(
            "ERROR: sqlite transaction function-scope policy violation(s):\n"
            + "\n".join(f"- {v}" for v in violations)
        )

    print("OK: sqlite transaction function-scope policy smoke passed.")


if __name__ == "__main__":
    main()
