#!/usr/bin/env python3
"""
Static smoke-check for businessbot legacy admin surface.

Policy:
- businessbot must not expose legacy admin commands from old in-bot admin flow
- businessbot must not keep legacy admin callback constants from old moderation/token menus

Run:
  python3 scripts/smoke_business_no_admin_commands_policy.py
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

# These commands belonged to the old in-businessbot admin flow and must stay absent.
FORBIDDEN_COMMANDS = (
    "moderation",
    "claim_token",
)

# Legacy callback/menu constants from removed businessbot-admin menu.
FORBIDDEN_LITERALS = (
    "CB_MOD_PAGE_PREFIX",
    "CB_MOD_APPROVE_PREFIX",
    "CB_MOD_REJECT_PREFIX",
    "CB_TOK_MENU",
    "CB_TOK_VIEW_SERVICES",
    "CB_TOK_VIEW_PLACES_PREFIX",
    "CB_TOK_VIEW_PLACE_PREFIX",
    "CB_TOK_GEN_MENU",
    "CB_TOK_GEN_SERVICES",
    "CB_TOK_GEN_PLACES_PREFIX",
    "CB_TOKG_SERV_PAGE_PREFIX",
    "CB_TOKG_SERV_PICK_PREFIX",
    "CB_TOKG_PLACE_PAGE_PREFIX",
    "CB_TOKG_PLACE_PICK_PREFIX",
    "CB_TOKG_BULK_CONFIRM_PREFIX",
)


def main() -> None:
    if not HANDLERS_PATH.exists():
        raise SystemExit(f"ERROR: file not found: {HANDLERS_PATH}")

    text = HANDLERS_PATH.read_text(encoding="utf-8")
    violations: list[str] = []

    for command in FORBIDDEN_COMMANDS:
        pattern = rf'@router\.message\(Command\("{re.escape(command)}"\)\)'
        if re.search(pattern, text):
            violations.append(f"forbidden command route found: /{command}")

    for literal in FORBIDDEN_LITERALS:
        if re.search(rf"\b{re.escape(literal)}\b", text):
            violations.append(f"forbidden legacy admin symbol found: {literal}")

    if violations:
        msg = "\n".join(f"- {item}" for item in violations)
        raise SystemExit(
            f"ERROR: business legacy admin-surface policy violation(s):\n{msg}"
        )

    print("OK: business no-admin-commands policy smoke passed.")


if __name__ == "__main__":
    main()
