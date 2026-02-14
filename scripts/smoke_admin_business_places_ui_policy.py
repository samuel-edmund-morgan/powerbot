#!/usr/bin/env python3
"""
Static smoke-check for admin business places UI flow.

Policy:
- place-detail UI exposes publish/hide/delete/reject-owner/edit/promo actions
- callbacks for those actions exist and are admin-guarded
- destructive actions keep explicit confirm screens before execution

Run:
  python3 scripts/smoke_admin_business_places_ui_policy.py
"""

from __future__ import annotations

from pathlib import Path
import re


def _resolve_handlers_path() -> Path:
    candidates = []
    try:
        candidates.append(Path(__file__).resolve().parents[1] / "src/admin/handlers.py")
    except Exception:
        pass
    candidates.extend([
        Path.cwd() / "src/admin/handlers.py",
        Path("/app/src/admin/handlers.py"),
    ])
    for path in candidates:
        if path.exists():
            return path
    return candidates[0] if candidates else Path("src/admin/handlers.py")


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


def _extract_callback_block(text: str, decorator_snippet: str, func_name: str) -> str:
    marker = f"@router.callback_query({decorator_snippet})"
    start = text.find(marker)
    if start < 0:
        return ""
    tail = text[start:]
    fn_marker = f"async def {func_name}("
    fn_pos = tail.find(fn_marker)
    if fn_pos < 0:
        return ""
    after_fn = tail[fn_pos:]
    m = re.search(
        r"^\n(?:@router\.callback_query\(|async\s+def\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(|def\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\()",
        after_fn,
        flags=re.MULTILINE,
    )
    body = after_fn if not m else after_fn[: m.start()]
    return marker + "\n" + body


def main() -> None:
    if not HANDLERS_PATH.exists():
        raise SystemExit(f"ERROR: file not found: {HANDLERS_PATH}")

    text = HANDLERS_PATH.read_text(encoding="utf-8")
    violations: list[str] = []

    required_constants = (
        "CB_BIZ_PLACES_PUBLISH_PREFIX",
        "CB_BIZ_PLACES_HIDE_PREFIX",
        "CB_BIZ_PLACES_HIDE_CONFIRM_PREFIX",
        "CB_BIZ_PLACES_DELETE_PREFIX",
        "CB_BIZ_PLACES_DELETE_CONFIRM_PREFIX",
        "CB_BIZ_PLACES_REJECT_OWNER_PREFIX",
        "CB_BIZ_PLACES_REJECT_OWNER_CONFIRM_PREFIX",
        "CB_BIZ_PLACES_EDIT_MENU_PREFIX",
        "CB_BIZ_PLACES_PROMO_MENU_PREFIX",
        "CB_BIZ_PLACES_PROMO_SET_PREFIX",
    )
    for const in required_constants:
        if re.search(rf"^\s*{re.escape(const)}\s*=", text, flags=re.MULTILINE) is None:
            violations.append(f"missing constant: {const}")

    detail_body = _extract_function_body(text, "_render_biz_place_detail")
    if not detail_body:
        violations.append("cannot locate _render_biz_place_detail()")
    else:
        for token in (
            "CB_BIZ_PLACES_HIDE_PREFIX",
            "CB_BIZ_PLACES_PUBLISH_PREFIX",
            "CB_BIZ_PLACES_DELETE_PREFIX",
            "CB_BIZ_PLACES_REJECT_OWNER_PREFIX",
            "CB_BIZ_PLACES_EDIT_MENU_PREFIX",
            "CB_BIZ_PLACES_PROMO_MENU_PREFIX",
        ):
            if token not in detail_body:
                violations.append(f"_render_biz_place_detail missing action token: {token}")

    callbacks: tuple[tuple[str, str], ...] = (
        ("F.data.startswith(CB_BIZ_PLACES_PUBLISH_PREFIX)", "cb_biz_places_publish"),
        ("F.data.startswith(CB_BIZ_PLACES_HIDE_PREFIX)", "cb_biz_places_hide_confirm_screen"),
        ("F.data.startswith(CB_BIZ_PLACES_HIDE_CONFIRM_PREFIX)", "cb_biz_places_hide"),
        ("F.data.startswith(CB_BIZ_PLACES_DELETE_PREFIX)", "cb_biz_places_delete_confirm_screen"),
        ("F.data.startswith(CB_BIZ_PLACES_DELETE_CONFIRM_PREFIX)", "cb_biz_places_delete"),
        ("F.data.startswith(CB_BIZ_PLACES_REJECT_OWNER_PREFIX)", "cb_biz_places_reject_owner_confirm_screen"),
        ("F.data.startswith(CB_BIZ_PLACES_REJECT_OWNER_CONFIRM_PREFIX)", "cb_biz_places_reject_owner"),
        ("F.data.startswith(CB_BIZ_PLACES_EDIT_MENU_PREFIX)", "cb_biz_places_edit_menu"),
        ("F.data.startswith(CB_BIZ_PLACES_PROMO_MENU_PREFIX)", "cb_biz_places_promo_menu"),
        ("F.data.startswith(CB_BIZ_PLACES_PROMO_SET_PREFIX)", "cb_biz_places_promo_set"),
    )

    for decorator, func_name in callbacks:
        block = _extract_callback_block(text, decorator, func_name)
        if not block:
            violations.append(f"missing callback handler: {func_name} ({decorator})")
            continue
        if "_require_admin_callback(callback)" not in block:
            violations.append(f"{func_name} must enforce _require_admin_callback(callback)")

    hide_confirm = _extract_function_body(text, "cb_biz_places_hide_confirm_screen")
    delete_confirm = _extract_function_body(text, "cb_biz_places_delete_confirm_screen")
    reject_confirm = _extract_function_body(text, "cb_biz_places_reject_owner_confirm_screen")

    if not hide_confirm or "CB_BIZ_PLACES_HIDE_CONFIRM_PREFIX" not in hide_confirm:
        violations.append("hide flow must have explicit confirm screen")
    if not delete_confirm or "CB_BIZ_PLACES_DELETE_CONFIRM_PREFIX" not in delete_confirm:
        violations.append("delete flow must have explicit confirm screen")
    if not reject_confirm or "CB_BIZ_PLACES_REJECT_OWNER_CONFIRM_PREFIX" not in reject_confirm:
        violations.append("reject-owner flow must have explicit confirm screen")

    if violations:
        msg = "\n".join(f"- {item}" for item in violations)
        raise SystemExit(f"ERROR: admin business places UI policy violation(s):\n{msg}")

    print("OK: admin business places UI policy smoke passed.")


if __name__ == "__main__":
    main()
