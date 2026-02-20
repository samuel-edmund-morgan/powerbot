#!/usr/bin/env python3
"""
Smoke test: businessbot QR access flow (Free lock vs Light access).

Validates:
- Free owner gets lock alert and redirect to plan menu.
- Light owner gets QR screen with resident deep-link + QR URL buttons.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
import time
import types
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


def _setup_temp_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_qr_access__",))
        conn.commit()
    finally:
        conn.close()


def _button_urls(reply_markup) -> list[str]:
    urls: list[str] = []
    if not reply_markup:
        return urls
    for row in getattr(reply_markup, "inline_keyboard", []):
        for button in row:
            url = getattr(button, "url", None)
            if url:
                urls.append(str(url))
    return urls


async def _run_checks() -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    from business.service import BusinessCabinetService  # noqa: WPS433
    from business.repository import BusinessRepository  # noqa: WPS433
    import business.handlers as bh  # noqa: WPS433

    repo = BusinessRepository()
    service = BusinessCabinetService(repository=repo)

    admin_id = 820001
    service.admin_ids.add(admin_id)
    bh.cabinet_service.admin_ids.add(admin_id)

    services = await repo.list_services()
    _assert(bool(services), "no services available in temp DB")
    service_id = int(services[0].get("id") or 0)
    _assert(service_id > 0, f"invalid service id: {services[0]}")

    stamp = int(time.time())
    owner_tg_user_id = 830000 + (stamp % 10000)
    created = await service.register_new_business(
        tg_user_id=owner_tg_user_id,
        service_id=service_id,
        place_name=f"QR Access {stamp}",
        description="qr smoke",
        address="addr",
    )
    owner = created.get("owner") or {}
    place = created.get("place") or {}
    owner_id = int(owner.get("id") or 0)
    place_id = int(place.get("id") or 0)
    _assert(owner_id > 0 and place_id > 0, f"invalid created objects: {created}")
    await service.approve_owner_request(admin_id, owner_id)

    # Monkeypatch render helpers to avoid Telegram API calls and capture intent.
    original_ui_render = bh.ui_render
    original_render_plan_menu = bh._render_place_plan_menu
    ui_calls: list[dict] = []
    plan_calls: list[dict] = []
    answer_calls: list[tuple[tuple, dict]] = []

    async def _fake_ui_render(bot, *, chat_id, prefer_message_id=None, text="", reply_markup=None, **kwargs):
        ui_calls.append(
            {
                "chat_id": chat_id,
                "prefer_message_id": prefer_message_id,
                "text": str(text),
                "reply_markup": reply_markup,
                "kwargs": kwargs,
            }
        )
        return SimpleNamespace(message_id=prefer_message_id or 0)

    async def _fake_render_place_plan_menu(message, *, tg_user_id, place_id, source, prefer_message_id, notice=None):
        plan_calls.append(
            {
                "tg_user_id": int(tg_user_id),
                "place_id": int(place_id),
                "source": str(source),
                "prefer_message_id": int(prefer_message_id or 0),
                "notice": str(notice or ""),
            }
        )
        return None

    class _DummyCallback:
        def __init__(self, user_id: int, place_id: int):
            self.data = f"{bh.CB_QR_OPEN_PREFIX}{place_id}"
            self.from_user = SimpleNamespace(id=int(user_id))
            self.message = SimpleNamespace(
                chat=SimpleNamespace(id=910001),
                message_id=77,
                bot=SimpleNamespace(),
            )

        async def answer(self, *args, **kwargs):
            answer_calls.append((args, kwargs))

    bh.ui_render = _fake_ui_render
    bh._render_place_plan_menu = _fake_render_place_plan_menu
    try:
        # Free branch: lock alert + redirect to plan menu.
        answer_calls.clear()
        plan_calls.clear()
        ui_calls.clear()
        cb_free = _DummyCallback(owner_tg_user_id, place_id)
        await bh.cb_open_place_qr(cb_free)

        _assert(answer_calls, "free branch must call callback.answer")
        free_msg = str(answer_calls[0][0][0] if answer_calls[0][0] else "")
        _assert("QR голосування доступний" in free_msg, f"free lock alert mismatch: {answer_calls[0]}")
        _assert(bool(answer_calls[0][1].get("show_alert")), f"free lock must use show_alert=True: {answer_calls[0]}")
        _assert(plan_calls, "free branch must redirect to plan menu")
        _assert(
            "QR голосування доступний" in str(plan_calls[0].get("notice") or ""),
            f"free plan notice mismatch: {plan_calls[0]}",
        )
        _assert(not ui_calls, "free branch must not render QR screen")

        # Light branch: QR screen rendered.
        await service.change_subscription_tier(owner_tg_user_id, place_id, "light")
        answer_calls.clear()
        plan_calls.clear()
        ui_calls.clear()
        cb_light = _DummyCallback(owner_tg_user_id, place_id)
        await bh.cb_open_place_qr(cb_light)

        _assert(not plan_calls, "light branch must not redirect to plan menu")
        _assert(ui_calls, "light branch must render QR screen")
        qr_screen = ui_calls[0]
        text = str(qr_screen.get("text") or "")
        _assert("QR голосування" in text, f"missing QR title in light screen: {text}")
        _assert("Deep-link" in text, f"missing deep-link text in light screen: {text}")
        expected_link = f"https://t.me/{str(bh.CFG.bot_username).strip().lstrip('@')}?start=place_{place_id}"
        _assert(expected_link in text, f"deep-link mismatch: expected {expected_link} in {text}")

        urls = _button_urls(qr_screen.get("reply_markup"))
        _assert(any(url == expected_link for url in urls), f"deep-link URL button missing: urls={urls}")
        _assert(
            any(url.startswith("https://api.qrserver.com/v1/create-qr-code/") for url in urls),
            f"QR URL button missing: urls={urls}",
        )
    finally:
        bh.ui_render = original_ui_render
        bh._render_place_plan_menu = original_render_plan_menu


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-qr-access-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "820001")
        os.environ["BUSINESS_MODE"] = "1"
        os.environ["BOT_USERNAME"] = "resident_smoke_bot"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: business QR access flow smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
