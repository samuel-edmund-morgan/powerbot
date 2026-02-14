#!/usr/bin/env python3
"""
Static smoke-check for admin business claim-tokens UI flow.

Policy:
- admin handlers must expose full callback flow for claim-token management:
  menu -> list/gen -> services -> places -> token open/rotate
- claim-token screens must include token payload fields and navigation callbacks
- each claim-token callback handler must enforce admin access via
  `_require_admin_callback(callback)`

Run:
  python3 scripts/smoke_admin_business_claim_tokens_ui_policy.py
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


def _extract_decorated_function_block(text: str, decorator_snippet: str, func_name: str) -> str:
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
        "CB_BIZ_TOK_MENU",
        "CB_BIZ_TOK_LIST",
        "CB_BIZ_TOK_GEN",
        "CB_BIZ_TOK_GEN_ALL",
        "CB_BIZ_TOK_GEN_ALL_CONFIRM",
        "CB_BIZ_TOKV_SVC_PAGE_PREFIX",
        "CB_BIZ_TOKV_SVC_PICK_PREFIX",
        "CB_BIZ_TOKV_PLACE_PAGE_PREFIX",
        "CB_BIZ_TOKV_PLACE_OPEN_PREFIX",
        "CB_BIZ_TOKV_PLACE_ROTATE_PREFIX",
        "CB_BIZ_TOKG_SVC_PAGE_PREFIX",
        "CB_BIZ_TOKG_SVC_PICK_PREFIX",
        "CB_BIZ_TOKG_PLACE_PAGE_PREFIX",
        "CB_BIZ_TOKG_PLACE_ROTATE_PREFIX",
    )
    for const in required_constants:
        if re.search(rf"^\s*{re.escape(const)}\s*=", text, flags=re.MULTILINE) is None:
            violations.append(f"missing constant: {const}")

    # Menu keyboard contract.
    menu_body = _extract_function_body(text, "_biz_tokens_menu_keyboard")
    if not menu_body:
        violations.append("cannot locate _biz_tokens_menu_keyboard()")
    else:
        for token in ("CB_BIZ_TOK_LIST", "CB_BIZ_TOK_GEN", "CB_BIZ_MENU", "admin_refresh"):
            if token not in menu_body:
                violations.append(f"_biz_tokens_menu_keyboard missing token: {token}")

    required_callbacks: tuple[tuple[str, str], ...] = (
        ("F.data == CB_BIZ_TOK_MENU", "cb_biz_tok_menu"),
        ("F.data == CB_BIZ_TOK_LIST", "cb_biz_tok_list"),
        ("F.data == CB_BIZ_TOK_GEN", "cb_biz_tok_gen"),
        ("F.data == CB_BIZ_TOK_GEN_ALL", "cb_biz_tok_gen_all"),
        ("F.data == CB_BIZ_TOK_GEN_ALL_CONFIRM", "cb_biz_tok_gen_all_confirm"),
        ("F.data.startswith(CB_BIZ_TOKV_SVC_PAGE_PREFIX)", "cb_biz_tokv_service_page"),
        ("F.data.startswith(CB_BIZ_TOKV_SVC_PICK_PREFIX)", "cb_biz_tokv_service_pick"),
        ("F.data.startswith(CB_BIZ_TOKV_PLACE_PAGE_PREFIX)", "cb_biz_tokv_place_page"),
        ("F.data.startswith(CB_BIZ_TOKV_PLACE_OPEN_PREFIX)", "cb_biz_tokv_place_open"),
        ("F.data.startswith(CB_BIZ_TOKV_PLACE_ROTATE_PREFIX)", "cb_biz_tokv_place_rotate"),
        ("F.data.startswith(CB_BIZ_TOKG_SVC_PAGE_PREFIX)", "cb_biz_tokg_service_page"),
        ("F.data.startswith(CB_BIZ_TOKG_SVC_PICK_PREFIX)", "cb_biz_tokg_service_pick"),
        ("F.data.startswith(CB_BIZ_TOKG_PLACE_PAGE_PREFIX)", "cb_biz_tokg_place_page"),
        ("F.data.startswith(CB_BIZ_TOKG_PLACE_ROTATE_PREFIX)", "cb_biz_tokg_place_rotate"),
    )

    for decorator, func_name in required_callbacks:
        block = _extract_decorated_function_block(text, decorator, func_name)
        if not block:
            violations.append(f"missing callback handler: {func_name} ({decorator})")
            continue
        if "_require_admin_callback(callback)" not in block:
            violations.append(f"{func_name} must enforce _require_admin_callback(callback)")

    open_body = _extract_function_body(text, "cb_biz_tokv_place_open")
    if not open_body:
        violations.append("cannot locate cb_biz_tokv_place_open()")
    else:
        for token in ("Token:", "Expires:", "CB_BIZ_TOKV_PLACE_ROTATE_PREFIX", "CB_BIZ_TOKV_PLACE_PAGE_PREFIX", "CB_BIZ_TOK_MENU"):
            if token not in open_body:
                violations.append(f"cb_biz_tokv_place_open missing token: {token}")

    rotate_view_body = _extract_function_body(text, "cb_biz_tokv_place_rotate")
    if not rotate_view_body:
        violations.append("cannot locate cb_biz_tokv_place_rotate()")
    else:
        for token in ("Token:", "Expires:", "CB_BIZ_TOKV_PLACE_PAGE_PREFIX", "CB_BIZ_TOK_MENU"):
            if token not in rotate_view_body:
                violations.append(f"cb_biz_tokv_place_rotate missing token: {token}")

    rotate_gen_body = _extract_function_body(text, "cb_biz_tokg_place_rotate")
    if not rotate_gen_body:
        violations.append("cannot locate cb_biz_tokg_place_rotate()")
    else:
        for token in ("Token:", "Expires:", "CB_BIZ_TOKG_PLACE_PAGE_PREFIX", "CB_BIZ_TOK_GEN", "CB_BIZ_TOK_MENU"):
            if token not in rotate_gen_body:
                violations.append(f"cb_biz_tokg_place_rotate missing token: {token}")

    if violations:
        msg = "\n".join(f"- {item}" for item in violations)
        raise SystemExit(f"ERROR: admin claim-tokens UI policy violation(s):\n{msg}")

    print("OK: admin claim-tokens UI policy smoke passed.")


if __name__ == "__main__":
    main()
