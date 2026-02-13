#!/usr/bin/env python3
"""
Static smoke-check: business bot single-message policy.

Policy goals:
- Business handlers should render UI through `business.ui.render` (`ui_render` alias),
  not direct `message.answer(...)` / `bot.send_message(...)`.
- Allowed direct `.answer(...)` calls:
  - callback answer acknowledgements (`callback.answer(...)`)
  - pre-checkout acknowledgements (`pre_checkout_query.answer(...)`)
  - health probe response (`cmd_health`: `message.answer("ok")`)

Run:
  python3 scripts/smoke_business_single_message_policy.py
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


HANDLERS_FILE = _resolve("src/business/handlers.py")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _read(path: Path) -> str:
    _assert(path.exists(), f"file not found: {path}")
    return path.read_text(encoding="utf-8")


class PolicyVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.current_func: str | None = None
        self.violations: list[str] = []

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        prev = self.current_func
        self.current_func = node.name
        self.generic_visit(node)
        self.current_func = prev

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        if isinstance(func, ast.Attribute):
            attr = func.attr
            owner = func.value
            owner_name = owner.id if isinstance(owner, ast.Name) else None

            if attr == "answer":
                allowed = False
                if owner_name in {"callback", "pre_checkout_query"}:
                    allowed = True
                elif owner_name == "message" and self.current_func == "cmd_health":
                    # Health command intentionally uses plain response.
                    allowed = True
                if not allowed:
                    self.violations.append(
                        f"{self.current_func or '<module>'}: forbidden direct `.answer(...)` call"
                    )

            if attr in {"send_message", "edit_message_text", "answer_document"}:
                self.violations.append(
                    f"{self.current_func or '<module>'}: forbidden direct `{attr}(...)` call"
                )

        self.generic_visit(node)


def main() -> None:
    source = _read(HANDLERS_FILE)
    tree = ast.parse(source)
    visitor = PolicyVisitor()
    visitor.visit(tree)

    if visitor.violations:
        raise SystemExit(
            "ERROR: business single-message policy violation(s):\n"
            + "\n".join(f"- {msg}" for msg in visitor.violations)
        )

    print("OK: business single-message policy smoke passed.")


if __name__ == "__main__":
    main()
