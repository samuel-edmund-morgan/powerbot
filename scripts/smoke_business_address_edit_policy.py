#!/usr/bin/env python3
"""
Static smoke-check: businessbot address edit flow must use building picker.

Policy:
- address edit starts from `EditPlaceStates.waiting_address_building`
- building is selected via callbacks, not free-text direct address overwrite
- details step appends to selected building label

Run:
  python3 scripts/smoke_business_address_edit_policy.py
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
    candidates.extend([
        Path.cwd() / "src/business/handlers.py",
        Path("/app/src/business/handlers.py"),
    ])
    for path in candidates:
        if path.exists():
            return path
    return candidates[0] if candidates else Path("src/business/handlers.py")


HANDLERS_PATH = _resolve_handlers_path()


def _extract_function_body(text: str, func_name: str) -> str:
    marker = f"def {func_name}("
    alt_marker = f"async def {func_name}("
    start = text.find(marker)
    if start < 0:
        start = text.find(alt_marker)
    if start < 0:
        return ""
    tail = text[start:]
    m = re.search(
        r"^\n(?:async\s+def\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(|def\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\()",
        tail,
        flags=re.MULTILINE,
    )
    return tail if not m else tail[: m.start()]


def main() -> None:
    if not HANDLERS_PATH.exists():
        raise SystemExit(f"ERROR: file not found: {HANDLERS_PATH}")

    text = HANDLERS_PATH.read_text(encoding="utf-8")
    violations: list[str] = []

    required_tokens = (
        "CB_EDIT_BUILDING_PICK_PREFIX",
        "CB_EDIT_BUILDING_CHANGE_PREFIX",
        "EditPlaceStates.waiting_address_building",
        "EditPlaceStates.waiting_address_details",
        "send_edit_building_picker",
    )
    for token in required_tokens:
        if token not in text:
            violations.append(f"missing token: {token}")

    cb_edit_field_pick = _extract_function_body(text, "cb_edit_field_pick")
    if not cb_edit_field_pick:
        violations.append("cannot locate cb_edit_field_pick()")
    else:
        if "if field == \"address\":" not in cb_edit_field_pick:
            violations.append("cb_edit_field_pick must branch address field explicitly")
        if "await state.set_state(EditPlaceStates.waiting_address_building)" not in cb_edit_field_pick:
            violations.append("address branch must set waiting_address_building")
        if "await send_edit_building_picker(" not in cb_edit_field_pick:
            violations.append("address branch must call send_edit_building_picker")

    cb_pick = _extract_function_body(text, "cb_edit_building_pick")
    if not cb_pick:
        violations.append("cannot locate cb_edit_building_pick()")
    else:
        if "await state.set_state(EditPlaceStates.waiting_address_details)" not in cb_pick:
            violations.append("cb_edit_building_pick must transition to waiting_address_details")

    cb_change = _extract_function_body(text, "cb_edit_building_change")
    if not cb_change:
        violations.append("cannot locate cb_edit_building_change()")
    else:
        if "await state.set_state(EditPlaceStates.waiting_address_building)" not in cb_change:
            violations.append("cb_edit_building_change must transition back to waiting_address_building")
        if "await send_edit_building_picker(" not in cb_change:
            violations.append("cb_edit_building_change must reopen building picker")

    details_handler = _extract_function_body(text, "edit_place_address_details")
    if not details_handler:
        violations.append("cannot locate edit_place_address_details()")
    else:
        for token in (
            "building_label",
            "address = building_label",
            "address = f\"{building_label}, {details}\"",
            'field="address"',
        ):
            if token not in details_handler:
                violations.append(f"edit_place_address_details missing token: {token}")

    if violations:
        msg = "\n".join(f"- {item}" for item in violations)
        raise SystemExit(f"ERROR: business address edit policy violation(s):\n{msg}")

    print("OK: business address edit policy smoke passed.")


if __name__ == "__main__":
    main()
