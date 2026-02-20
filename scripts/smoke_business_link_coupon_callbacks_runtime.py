#!/usr/bin/env python3
"""
Dynamic smoke test: resident link/coupon callback runtime contract.

Validates:
- `plink_` callback redirects to normalized URL and records `link` click.
- `pcoupon_` callback shows promo alert and records `coupon_open` click.
- negative coupon path (not verified / no promo) does not increment counters.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


def _resolve_repo_root() -> Path:
    candidates: list[Path] = []
    try:
        candidates.append(Path(__file__).resolve().parents[1])
    except Exception:
        pass
    candidates.extend([Path.cwd(), Path("/app"), Path("/workspace")])
    for root in candidates:
        if (root / "schema.sql").exists() and (root / "src").exists():
            return root
    raise FileNotFoundError("Cannot locate repo root with schema.sql and src/")


REPO_ROOT = _resolve_repo_root()
SCHEMA_SQL = REPO_ROOT / "schema.sql"


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _setup_temp_db(db_path: Path) -> int:
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_link_coupon_cb__",))
        service_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            """
            INSERT INTO places(
                service_id, name, description, address, keywords,
                is_published, business_enabled, is_verified, verified_tier, verified_until,
                link_url, promo_code
            ) VALUES(?, 'Link Coupon Smoke', 'smoke', 'addr', 'kw', 1, 1, 1, 'light', ?, 't.me/smoke_link', 'SAVE10')
            """,
            (service_id, now_iso),
        )
        place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.commit()
        return place_id
    finally:
        conn.close()


async def _get_click_sum(place_id: int, action: str) -> int:
    from database import open_db  # noqa: WPS433

    async with open_db() as db:
        async with db.execute(
            """
            SELECT COALESCE(SUM(cnt), 0)
              FROM place_clicks_daily
             WHERE place_id = ? AND action = ?
            """,
            (int(place_id), str(action)),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0] if row and row[0] is not None else 0)


async def _run_checks(place_id: int) -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    from database import open_db  # noqa: WPS433
    import handlers as resident_handlers  # noqa: WPS433

    safe_calls: list[dict] = []

    class _DummyMessage:
        def __init__(self) -> None:
            self.answers: list[dict] = []

        async def answer(self, text: str, reply_markup=None):
            self.answers.append({"text": str(text), "reply_markup": reply_markup})
            return None

    class _DummyCallback:
        def __init__(self, data: str, message: _DummyMessage):
            self.data = data
            self.message = message
            self.from_user = SimpleNamespace(id=960001, username="link_coupon_smoke", first_name="LinkCoupon")

        async def answer(self, *_args, **_kwargs):
            return True

    original_safe_callback_answer = resident_handlers.safe_callback_answer

    async def _fake_safe_callback_answer(callback, text=None, **kwargs):
        safe_calls.append({"text": None if text is None else str(text), "kwargs": dict(kwargs)})
        return True

    resident_handlers.safe_callback_answer = _fake_safe_callback_answer
    try:
        # 1) Link callback success path.
        msg_link = _DummyMessage()
        cb_link = _DummyCallback(f"plink_{int(place_id)}", msg_link)
        before_link = await _get_click_sum(int(place_id), "link")
        await resident_handlers.cb_place_link_open(cb_link)
        after_link = await _get_click_sum(int(place_id), "link")
        _assert(after_link == before_link + 1, f"link click counter mismatch: {before_link}->{after_link}")
        _assert(len(msg_link.answers) == 0, f"link success should not use message fallback: {msg_link.answers}")
        _assert(
            str(safe_calls[-1]["kwargs"].get("url") or "") == "https://t.me/smoke_link",
            f"link redirect mismatch: {safe_calls[-1]}",
        )

        # 2) Coupon callback success path.
        msg_coupon = _DummyMessage()
        cb_coupon = _DummyCallback(f"pcoupon_{int(place_id)}", msg_coupon)
        before_coupon = await _get_click_sum(int(place_id), "coupon_open")
        await resident_handlers.cb_place_coupon_open(cb_coupon)
        after_coupon = await _get_click_sum(int(place_id), "coupon_open")
        _assert(after_coupon == before_coupon + 1, f"coupon click counter mismatch: {before_coupon}->{after_coupon}")
        _assert(len(msg_coupon.answers) == 0, f"coupon handler should not use message.answer: {msg_coupon.answers}")
        _assert(
            str(safe_calls[-1]["text"] or "") == "🎟 Промокод: SAVE10",
            f"coupon success alert text mismatch: {safe_calls[-1]}",
        )
        _assert(bool(safe_calls[-1]["kwargs"].get("show_alert")), f"coupon success must be show_alert: {safe_calls[-1]}")

        # 3) Coupon negative path: place no longer verified.
        async with open_db() as db:
            await db.execute("UPDATE places SET is_verified = 0, promo_code = '' WHERE id = ?", (int(place_id),))
            await db.commit()

        msg_coupon_bad = _DummyMessage()
        cb_coupon_bad = _DummyCallback(f"pcoupon_{int(place_id)}", msg_coupon_bad)
        before_coupon_bad = await _get_click_sum(int(place_id), "coupon_open")
        await resident_handlers.cb_place_coupon_open(cb_coupon_bad)
        after_coupon_bad = await _get_click_sum(int(place_id), "coupon_open")
        _assert(
            after_coupon_bad == before_coupon_bad,
            f"coupon negative path must not increment counter: {before_coupon_bad}->{after_coupon_bad}",
        )
        _assert(
            str(safe_calls[-1]["text"] or "") == "Промокод для цього закладу недоступний.",
            f"coupon negative alert mismatch: {safe_calls[-1]}",
        )
        _assert(bool(safe_calls[-1]["kwargs"].get("show_alert")), f"coupon negative must use show_alert: {safe_calls[-1]}")
    finally:
        resident_handlers.safe_callback_answer = original_safe_callback_answer


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-link-coupon-runtime-"))
    try:
        db_path = tmpdir / "state.db"
        place_id = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ["BUSINESS_MODE"] = "1"
        os.environ.setdefault("ADMIN_IDS", "1")

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks(int(place_id)))
        print("OK: business link/coupon callbacks runtime smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
