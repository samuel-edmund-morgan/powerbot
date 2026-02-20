#!/usr/bin/env python3
"""
Static smoke-check: owner place-card actions must be built from a single source.

Policy:
- `_build_owner_place_card_action_rows(...)` exists and contains single QR-kit/support rows.
- both `render_place_card_updated(...)` and `cb_my_business_open(...)` use this helper.
- `cb_my_business_open(...)` must not duplicate legacy action-row construction logic.
"""

from __future__ import annotations

from pathlib import Path
import re


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


def _extract_function_body(text: str, func_name: str) -> str:
    marker = rf"(?:async\s+)?def\s+{re.escape(func_name)}\s*\("
    m = re.search(marker, text)
    if not m:
        return ""
    tail = text[m.start() :]
    next_top = re.search(r"^\n(?:@router\.[^\n]*|async\s+def\s+\w+\s*\(|def\s+\w+\s*\()", tail, flags=re.MULTILINE)
    return tail if not next_top else tail[: next_top.start()]


def _must_contain(body: str, token: str, *, errors: list[str], label: str) -> None:
    if token not in body:
        errors.append(f"{label}: missing token: {token}")


def _must_not_contain(body: str, token: str, *, errors: list[str], label: str) -> None:
    if token in body:
        errors.append(f"{label}: forbidden legacy token found: {token}")


def main() -> None:
    handlers_path = _resolve_handlers_path()
    if not handlers_path.exists():
        raise SystemExit(f"ERROR: file not found: {handlers_path}")

    text = handlers_path.read_text(encoding="utf-8")
    errors: list[str] = []

    helper_body = _extract_function_body(text, "_build_owner_place_card_action_rows")
    render_body = _extract_function_body(text, "render_place_card_updated")
    open_body = _extract_function_body(text, "cb_my_business_open")

    if not helper_body:
        errors.append("missing function: _build_owner_place_card_action_rows")
    if not render_body:
        errors.append("missing function: render_place_card_updated")
    if not open_body:
        errors.append("missing function: cb_my_business_open")

    if helper_body:
        _must_contain(
            helper_body,
            'qr_kit_cb = f"{CB_QR_KIT_OPEN_PREFIX}{place_id}"',
            errors=errors,
            label="_build_owner_place_card_action_rows",
        )
        _must_contain(
            helper_body,
            'support_cb = f"{CB_PARTNER_SUPPORT_PREFIX}{place_id}"',
            errors=errors,
            label="_build_owner_place_card_action_rows",
        )
        if helper_body.count('CB_QR_KIT_OPEN_PREFIX') != 1:
            errors.append("_build_owner_place_card_action_rows: expected exactly one QR-kit action block")
        if helper_body.count('CB_PARTNER_SUPPORT_PREFIX') != 1:
            errors.append("_build_owner_place_card_action_rows: expected exactly one priority-support action block")

    required_call = "keyboard_rows = _build_owner_place_card_action_rows(place_id=place_id, item=item)"
    if render_body:
        _must_contain(render_body, required_call, errors=errors, label="render_place_card_updated")
    if open_body:
        _must_contain(open_body, required_call, errors=errors, label="cb_my_business_open")
        _must_not_contain(
            open_body,
            'can_edit = _has_active_paid_subscription(item)',
            errors=errors,
            label="cb_my_business_open",
        )
        _must_not_contain(
            open_body,
            'qr_text = "🔳 QR голосування"',
            errors=errors,
            label="cb_my_business_open",
        )
        _must_not_contain(
            open_body,
            'edit_text = "✏️ Редагувати"',
            errors=errors,
            label="cb_my_business_open",
        )

    if errors:
        raise SystemExit("ERROR: owner card actions policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business owner card actions policy smoke passed.")


if __name__ == "__main__":
    main()

