#!/usr/bin/env python3
"""
Static smoke-check: Free-owner "suggest edit" moderation flow in businessbot.

Policy:
- Free owner card contains `📝 Запропонувати правку` callback.
- Callback flow exists and enters FSM text state.
- Submission persists report via `create_place_report(...)`.
- Submission enqueues admin moderation alert via `create_admin_job("admin_place_report_alert", ...)`.
"""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: file not found: {path}")
    return path.read_text(encoding="utf-8")


def _must(text: str, token: str, *, errors: list[str]) -> None:
    if token not in text:
        errors.append(f"missing token: {token}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    text = _read(root / "src/business/handlers.py")
    errors: list[str] = []

    _must(text, 'CB_FREE_EDIT_REQUEST_PREFIX = "bfr:"', errors=errors)
    _must(text, 'CB_FREE_EDIT_REQUEST_CANCEL_PREFIX = "bfrc:"', errors=errors)
    _must(text, 'class FreeEditRequestStates(StatesGroup):', errors=errors)
    _must(text, 'text="📝 Запропонувати правку"', errors=errors)
    _must(text, '@router.callback_query(F.data.startswith(CB_FREE_EDIT_REQUEST_PREFIX))', errors=errors)
    _must(text, '@router.message(FreeEditRequestStates.waiting_text, F.text)', errors=errors)
    _must(text, "report = await create_place_report(", errors=errors)
    _must(text, '"admin_place_report_alert"', errors=errors)

    if errors:
        raise SystemExit("ERROR: business free edit request policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business free edit request policy smoke passed.")


if __name__ == "__main__":
    main()
