#!/usr/bin/env python3
"""
Static smoke-check for admin business moderation UI flow.

Policy:
- moderation screen must include owner tg contact link (tg://user?id=...)
- moderation keyboard must expose approve/reject callbacks
- moderation callbacks must enforce admin guard
- approve/reject handlers must notify owner via business bot helper

Run:
  python3 scripts/smoke_admin_business_moderation_ui_policy.py
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

    constants = (
        "CB_BIZ_MOD",
        "CB_BIZ_MOD_PAGE_PREFIX",
        "CB_BIZ_MOD_JUMP_PREFIX",
        "CB_BIZ_MOD_APPROVE_PREFIX",
        "CB_BIZ_MOD_REJECT_PREFIX",
    )
    for const in constants:
        if re.search(rf"^\s*{re.escape(const)}\s*=", text, flags=re.MULTILINE) is None:
            violations.append(f"missing constant: {const}")

    contact_body = _extract_function_body(text, "_format_tg_contact")
    if not contact_body:
        violations.append("cannot locate _format_tg_contact()")
    elif "tg://user?id=" not in contact_body:
        violations.append("_format_tg_contact must build tg://user link")

    kb_body = _extract_function_body(text, "_biz_moderation_keyboard")
    if not kb_body:
        violations.append("cannot locate _biz_moderation_keyboard()")
    else:
        for token in ("CB_BIZ_MOD_APPROVE_PREFIX", "CB_BIZ_MOD_REJECT_PREFIX"):
            if token not in kb_body:
                violations.append(f"_biz_moderation_keyboard missing token: {token}")

    render_body = _extract_function_body(text, "_render_business_moderation")
    if not render_body:
        violations.append("cannot locate _render_business_moderation()")
    else:
        for token in ("user_contact = _format_tg_contact", "Власник: {user_contact}"):
            if token not in render_body:
                violations.append(f"_render_business_moderation missing token: {token}")

    callbacks: tuple[tuple[str, str], ...] = (
        ("F.data == CB_BIZ_MOD", "cb_business_moderation"),
        ("F.data.startswith(CB_BIZ_MOD_PAGE_PREFIX)", "cb_business_moderation_page"),
        ("F.data.startswith(CB_BIZ_MOD_JUMP_PREFIX)", "cb_business_moderation_jump"),
        ("F.data.startswith(CB_BIZ_MOD_APPROVE_PREFIX)", "cb_business_moderation_approve"),
        ("F.data.startswith(CB_BIZ_MOD_REJECT_PREFIX)", "cb_business_moderation_reject"),
    )
    for decorator, func_name in callbacks:
        block = _extract_callback_block(text, decorator, func_name)
        if not block:
            violations.append(f"missing callback handler: {func_name} ({decorator})")
            continue
        if "_require_admin_callback(callback)" not in block:
            violations.append(f"{func_name} must enforce _require_admin_callback(callback)")

    approve_body = _extract_function_body(text, "cb_business_moderation_approve")
    reject_body = _extract_function_body(text, "cb_business_moderation_reject")
    if not approve_body or "_notify_owner_via_business_bot(" not in approve_body:
        violations.append("cb_business_moderation_approve must notify owner via business bot")
    if not reject_body or "_notify_owner_via_business_bot(" not in reject_body:
        violations.append("cb_business_moderation_reject must notify owner via business bot")

    if violations:
        msg = "\n".join(f"- {item}" for item in violations)
        raise SystemExit(f"ERROR: admin business moderation UI policy violation(s):\n{msg}")

    print("OK: admin business moderation UI policy smoke passed.")


if __name__ == "__main__":
    main()
