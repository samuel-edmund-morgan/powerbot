#!/usr/bin/env python3
"""
Static smoke-check for admin owner-request alert UI policy.

Policy:
- `_handle_admin_owner_request_alert()` must deliver via `render_admin_ui(...)`
  (single-message aware), not direct send_message calls.
- `_owner_request_alert_keyboard()` must expose navigation callbacks:
  - jump to specific request (`abiz_mod_jump|...`)
  - open moderation queue (`abiz_mod`)
  - return to admin main menu (`admin_refresh`)

Run:
  python3 scripts/smoke_admin_owner_alert_ui_policy.py
"""

from __future__ import annotations

from pathlib import Path
import re


def _resolve_worker_path() -> Path:
    candidates = []
    try:
        candidates.append(Path(__file__).resolve().parents[1] / "src/admin_jobs_worker.py")
    except Exception:
        pass
    candidates.extend(
        [
            Path.cwd() / "src/admin_jobs_worker.py",
            Path("/app/src/admin_jobs_worker.py"),
        ]
    )
    for path in candidates:
        if path.exists():
            return path
    return candidates[0] if candidates else Path("src/admin_jobs_worker.py")


WORKER_PATH = _resolve_worker_path()


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
    if not WORKER_PATH.exists():
        raise SystemExit(f"ERROR: file not found: {WORKER_PATH}")

    text = WORKER_PATH.read_text(encoding="utf-8")
    violations: list[str] = []

    handler_body = _extract_function_body(text, "_handle_admin_owner_request_alert")
    if not handler_body:
        violations.append("cannot locate _handle_admin_owner_request_alert()")
    else:
        if "render_admin_ui(" not in handler_body:
            violations.append("_handle_admin_owner_request_alert must deliver via render_admin_ui(...)")
        forbidden_sends = (
            "admin_bot.send_message(",
            "Bot.send_message(",
            ".answer(",
        )
        for token in forbidden_sends:
            if token in handler_body:
                violations.append(
                    f"_handle_admin_owner_request_alert contains forbidden direct send token: {token}"
                )

    kb_body = _extract_function_body(text, "_owner_request_alert_keyboard")
    if not kb_body:
        violations.append("cannot locate _owner_request_alert_keyboard()")
    else:
        required_callbacks = (
            "abiz_mod_jump|",
            "abiz_mod",
            "admin_refresh",
        )
        for callback_token in required_callbacks:
            if callback_token not in kb_body:
                violations.append(f"_owner_request_alert_keyboard missing callback token: {callback_token}")

    if violations:
        msg = "\n".join(f"- {item}" for item in violations)
        raise SystemExit(f"ERROR: admin owner-alert UI policy violation(s):\n{msg}")

    print("OK: admin owner-alert UI policy smoke passed.")


if __name__ == "__main__":
    main()
