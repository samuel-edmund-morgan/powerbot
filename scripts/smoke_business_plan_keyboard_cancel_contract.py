#!/usr/bin/env python3
"""
Smoke test: business plan keyboard cancel/free contract.

Checks:
- paid active (not expired) -> show `🚫 Скасувати автопродовження` with `bp_cancel`.
- paid canceled (not expired) -> show `🚫 Автопродовження скасовано` with `bp_cancel` (explicit feedback, no silent noop).
- free/inactive -> show regular Free button (no cancel controls).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import types
import os


def _setup_import_path() -> None:
    repo_src: Path | None = None
    try:
        repo_src = Path(__file__).resolve().parents[1] / "src"
    except Exception:
        repo_src = None

    candidates: list[Path] = []
    if repo_src is not None:
        candidates.append(repo_src)
    candidates.extend([Path.cwd() / "src", Path("/app/src")])

    for candidate in candidates:
        if candidate.exists():
            sys.path.insert(0, str(candidate))
            return


_setup_import_path()


def _ensure_dotenv_stub() -> None:
    try:
        import dotenv  # noqa: F401
        return
    except Exception:
        pass
    if "dotenv" in sys.modules:
        return
    dotenv_stub = types.ModuleType("dotenv")

    def _noop_load_dotenv(*_args, **_kwargs) -> bool:
        return False

    dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
    sys.modules["dotenv"] = dotenv_stub


def _ensure_aiosqlite_stub() -> None:
    try:
        import aiosqlite  # noqa: F401
        return
    except Exception:
        pass
    if "aiosqlite" in sys.modules:
        return
    aiosqlite_stub = types.ModuleType("aiosqlite")

    class _Connection:
        pass

    class _Row:
        pass

    class _OperationalError(Exception):
        pass

    class _IntegrityError(Exception):
        pass

    async def _connect(*_args, **_kwargs):
        raise RuntimeError("aiosqlite stub is not meant to be used at runtime in this smoke.")

    aiosqlite_stub.Connection = _Connection  # type: ignore[attr-defined]
    aiosqlite_stub.Row = _Row  # type: ignore[attr-defined]
    aiosqlite_stub.OperationalError = _OperationalError  # type: ignore[attr-defined]
    aiosqlite_stub.IntegrityError = _IntegrityError  # type: ignore[attr-defined]
    aiosqlite_stub.connect = _connect  # type: ignore[attr-defined]
    sys.modules["aiosqlite"] = aiosqlite_stub


def _ensure_config_env() -> None:
    os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
    os.environ.setdefault("ADMIN_IDS", "1")
    os.environ.setdefault("BUSINESS_MODE", "1")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _buttons(kb) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for row in kb.inline_keyboard:
        for btn in row:
            out.append((str(getattr(btn, "text", "") or ""), str(getattr(btn, "callback_data", "") or "")))
    return out


def _find_first(rows: list[tuple[str, str]], text_part: str) -> tuple[str, str] | None:
    for text, cb in rows:
        if text_part in text:
            return text, cb
    return None


def main() -> None:
    _ensure_dotenv_stub()
    _ensure_aiosqlite_stub()
    _ensure_config_env()
    from business.handlers import build_plan_keyboard  # noqa: E402

    place_id = 77
    now = datetime.now(timezone.utc)
    future_iso = (now + timedelta(days=10)).isoformat()

    # Case 1: active paid -> cancel auto-renew CTA.
    kb_active = build_plan_keyboard(
        place_id,
        "light",
        current_status="active",
        current_expires_at=future_iso,
        source="card",
    )
    rows_active = _buttons(kb_active)
    cancel_btn = _find_first(rows_active, "Скасувати автопродовження")
    _assert(cancel_btn is not None, "active paid plan must show cancel auto-renew button")
    _assert(cancel_btn[1] == f"bp_cancel:{place_id}:card", f"unexpected cancel callback: {cancel_btn}")
    _assert(_find_first(rows_active, "Автопродовження скасовано") is None, "active paid plan must not show canceled badge button")

    # Case 2: canceled paid -> explicit canceled status button with bp_cancel callback.
    kb_canceled = build_plan_keyboard(
        place_id,
        "light",
        current_status="canceled",
        current_expires_at=future_iso,
        source="card",
    )
    rows_canceled = _buttons(kb_canceled)
    canceled_btn = _find_first(rows_canceled, "Автопродовження скасовано")
    _assert(canceled_btn is not None, "canceled paid plan must show canceled status button")
    _assert(canceled_btn[1] == f"bp_cancel:{place_id}:card", f"canceled button must use bp_cancel callback: {canceled_btn}")

    # Case 3: free/inactive -> regular Free button, no cancel controls.
    kb_free = build_plan_keyboard(
        place_id,
        "free",
        current_status="inactive",
        current_expires_at=None,
        source="card",
    )
    rows_free = _buttons(kb_free)
    _assert(any(cb == f"bp:{place_id}:free:card" for _, cb in rows_free), "free plan must expose regular free callback")
    _assert(_find_first(rows_free, "Скасувати автопродовження") is None, "free plan must not show cancel auto-renew button")
    _assert(_find_first(rows_free, "Автопродовження скасовано") is None, "free plan must not show canceled badge button")

    print("OK: business plan keyboard cancel contract smoke passed.")


if __name__ == "__main__":
    main()
