#!/usr/bin/env python3
"""
Static smoke-check for business bot UI policy.

Policy:
- business handlers must stay inline-only (no ReplyKeyboardMarkup/KeyboardButton usage)
- main menu must expose only owner actions (no legacy admin callbacks/buttons)
- each main menu callback must have a direct callback handler

Run:
  python3 scripts/smoke_business_ui_inline_menu_policy.py
"""

from __future__ import annotations

from pathlib import Path
import re


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

FORBIDDEN_TOKENS = {
    "ReplyKeyboardMarkup",
    "KeyboardButton",
}

MAIN_MENU_CALLBACKS = (
    "CB_MENU_ADD",
    "CB_MENU_ATTACH",
    "CB_MENU_MINE",
    "CB_MENU_PLANS",
)


def _extract_function_body(text: str, func_name: str) -> str:
    marker = f"def {func_name}("
    start = text.find(marker)
    if start < 0:
        return ""
    tail = text[start:]
    # Stop at next top-level decorator/def/async def.
    m = re.search(
        r"^\n(?:@router\.[^\n]*|async\s+def\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(|def\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\()",
        tail,
        flags=re.MULTILINE,
    )
    return tail if not m else tail[: m.start()]


def main() -> None:
    if not HANDLERS_PATH.exists():
        raise SystemExit(f"ERROR: file not found: {HANDLERS_PATH}")

    text = HANDLERS_PATH.read_text(encoding="utf-8")
    violations: list[str] = []

    for token in FORBIDDEN_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", text):
            violations.append(f"forbidden token in handlers: {token}")

    main_menu_body = _extract_function_body(text, "build_main_menu")
    if not main_menu_body:
        violations.append("cannot locate build_main_menu()")
    else:
        # Legacy admin callbacks must not be shown in business main menu.
        if "CB_MENU_MOD" in main_menu_body or "CB_MENU_TOKENS" in main_menu_body:
            violations.append("build_main_menu contains legacy admin callbacks")
        # Main menu should still expose core owner actions.
        for cb in MAIN_MENU_CALLBACKS:
            if cb not in main_menu_body:
                violations.append(f"build_main_menu missing expected callback: {cb}")

    for cb in MAIN_MENU_CALLBACKS:
        pattern = rf"@router\.callback_query\(F\.data == {cb}\)"
        if not re.search(pattern, text):
            violations.append(f"missing callback handler for {cb}")

    if violations:
        msg = "\n".join(f"- {item}" for item in violations)
        raise SystemExit(f"ERROR: business UI inline/menu policy violation(s):\n{msg}")

    print("OK: business UI inline/menu policy smoke passed.")


if __name__ == "__main__":
    main()
