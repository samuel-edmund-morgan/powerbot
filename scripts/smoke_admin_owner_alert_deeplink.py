#!/usr/bin/env python3
"""
Smoke test for admin owner-request alert deep-link/jump UI helpers.

What it validates:
- deep-link URL format for /start bmod_<request_id>
- alert text contains link when URL is provided
- alert keyboard has jump callback and optional deep-link button

Run:
  python3 scripts/smoke_admin_owner_alert_deeplink.py
"""

from __future__ import annotations

from pathlib import Path
import sys


def _setup_import_path() -> None:
    for candidate in (
        Path.cwd() / "src",  # local repo root
        Path("/app/src"),    # container path
    ):
        if candidate.exists():
            sys.path.insert(0, str(candidate))
            return


_setup_import_path()

from admin_jobs_worker import (  # noqa: E402
    _build_adminbot_start_url,
    _owner_request_alert_keyboard,
    _render_owner_request_alert_text,
)


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def main() -> None:
    request_id = 777
    deep_link = _build_adminbot_start_url("my_admin_bot", request_id)
    _assert(
        deep_link == f"https://t.me/my_admin_bot?start=bmod_{request_id}",
        f"unexpected deep-link: {deep_link}",
    )

    payload = {
        "request_id": request_id,
        "place_id": 120,
        "place_name": "Test Place",
        "owner_tg_user_id": 123456789,
        "from_label": "owner",
        "from_username": "owner_test",
        "source": "new_business",
        "created_at": "2026-02-11T20:00:00",
    }
    text = _render_owner_request_alert_text(payload, deep_link_url=deep_link)
    _assert("Швидкий перехід" in text, "alert text must include quick deep-link hint")
    _assert("start=bmod_777" in text, "alert text must include start payload link")

    kb = _owner_request_alert_keyboard(request_id=request_id, deep_link_url=deep_link)
    rows = kb.inline_keyboard
    _assert(len(rows) >= 3, "alert keyboard must contain jump + moderation + main menu rows")
    _assert(
        rows[0][0].callback_data == "abiz_mod_jump|777",
        f"unexpected jump callback: {rows[0][0].callback_data}",
    )
    _assert(
        any(btn.url == deep_link for row in rows for btn in row if getattr(btn, "url", None)),
        "alert keyboard must include deep-link URL button",
    )

    kb_no_link = _owner_request_alert_keyboard(request_id=request_id, deep_link_url=None)
    rows_no_link = kb_no_link.inline_keyboard
    _assert(
        not any(getattr(btn, "url", None) for row in rows_no_link for btn in row),
        "keyboard without deep-link must not contain URL buttons",
    )

    print("OK: admin owner-request alert deep-link smoke passed.")


if __name__ == "__main__":
    main()
