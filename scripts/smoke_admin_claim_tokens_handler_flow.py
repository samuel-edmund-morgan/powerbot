#!/usr/bin/env python3
"""
Dynamic smoke test: admin claim-tokens handler flow.

Validates callback chain for admin UI:
- view branch: menu -> list -> service -> place -> open token -> rotate token
- gen branch: menu -> generate -> service -> place -> rotate token
"""

from __future__ import annotations

import asyncio
import os
import re
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


def _setup_temp_db(db_path: Path) -> tuple[int, int]:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_claim_flow__",))
        service_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        place_name = f"Claim Flow Place {int(time.time())}"
        conn.execute(
            """
            INSERT INTO places(service_id, name, description, address, is_published, business_enabled)
            VALUES(?, ?, ?, ?, 1, 1)
            """,
            (service_id, place_name, "smoke", "addr"),
        )
        place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.commit()
        return service_id, place_id
    finally:
        conn.close()


def _find_callback(reply_markup, prefix: str) -> str | None:
    if not reply_markup:
        return None
    for row in getattr(reply_markup, "inline_keyboard", []):
        for button in row:
            cb = getattr(button, "callback_data", None)
            if isinstance(cb, str) and cb.startswith(prefix):
                return cb
    return None


def _extract_token(text: str) -> str:
    match = re.search(r"Token:\s*<code>([^<]+)</code>", str(text))
    if not match:
        raise AssertionError(f"token not found in text: {text}")
    return str(match.group(1))


async def _run_checks(db_path: Path, service_id: int, place_id: int, admin_id: int) -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    import admin.handlers as ah  # noqa: WPS433

    # Safety for import ordering in smoke environments.
    if int(admin_id) not in set(getattr(ah.CFG, "admin_ids", [])):
        ah.CFG.admin_ids.append(int(admin_id))
    ah.business_service.admin_ids.add(int(admin_id))

    render_calls: list[dict] = []
    answer_calls: list[tuple[tuple, dict]] = []
    original_render = ah.render

    async def _fake_render(bot, *, chat_id, text, reply_markup, prefer_message_id=None, force_new_message=False, **kwargs):
        render_calls.append(
            {
                "chat_id": int(chat_id),
                "text": str(text),
                "reply_markup": reply_markup,
                "prefer_message_id": int(prefer_message_id or 0),
                "force_new_message": bool(force_new_message),
                "kwargs": kwargs,
            }
        )
        return SimpleNamespace(message_id=prefer_message_id or 0)

    class _DummyCallback:
        def __init__(self, data: str):
            self.data = str(data)
            self.from_user = SimpleNamespace(id=int(admin_id), username="admin_smoke")
            self.bot = SimpleNamespace()
            self.message = SimpleNamespace(
                chat=SimpleNamespace(id=800001),
                message_id=77,
                bot=SimpleNamespace(),
            )

        async def answer(self, *args, **kwargs):
            answer_calls.append((args, kwargs))

    ah.render = _fake_render
    try:
        # ---- View branch: list -> open -> rotate
        await ah.cb_biz_tok_menu(_DummyCallback(ah.CB_BIZ_TOK_MENU))  # type: ignore[arg-type]
        _assert(render_calls, "menu step did not render")
        _assert("Коди прив'язки" in render_calls[-1]["text"], f"unexpected menu text: {render_calls[-1]}")

        await ah.cb_biz_tok_list(_DummyCallback(ah.CB_BIZ_TOK_LIST))  # type: ignore[arg-type]
        list_screen = render_calls[-1]
        _assert("Оберіть категорію" in list_screen["text"], f"unexpected list text: {list_screen}")
        svc_pick_cb = _find_callback(list_screen["reply_markup"], ah.CB_BIZ_TOKV_SVC_PICK_PREFIX)
        _assert(svc_pick_cb is not None, f"service pick callback not found; screen={list_screen}")
        _assert(
            f"{ah.CB_BIZ_TOKV_SVC_PICK_PREFIX}{service_id}|" in str(svc_pick_cb),
            f"service callback mismatch: expected service_id={service_id}, got={svc_pick_cb}",
        )

        await ah.cb_biz_tokv_service_pick(_DummyCallback(str(svc_pick_cb)))  # type: ignore[arg-type]
        places_screen = render_calls[-1]
        _assert("Оберіть заклад" in places_screen["text"], f"unexpected places text: {places_screen}")
        place_open_cb = _find_callback(places_screen["reply_markup"], ah.CB_BIZ_TOKV_PLACE_OPEN_PREFIX)
        _assert(place_open_cb is not None, f"place open callback not found; screen={places_screen}")
        _assert(
            f"{ah.CB_BIZ_TOKV_PLACE_OPEN_PREFIX}{place_id}|" in str(place_open_cb),
            f"place open callback mismatch: expected place_id={place_id}, got={place_open_cb}",
        )

        await ah.cb_biz_tokv_place_open(_DummyCallback(str(place_open_cb)))  # type: ignore[arg-type]
        open_screen = render_calls[-1]
        _assert("Код прив'язки" in open_screen["text"], f"unexpected open text: {open_screen}")
        token_1 = _extract_token(str(open_screen["text"]))
        rotate_cb = _find_callback(open_screen["reply_markup"], ah.CB_BIZ_TOKV_PLACE_ROTATE_PREFIX)
        _assert(rotate_cb is not None, f"rotate callback not found; screen={open_screen}")

        await ah.cb_biz_tokv_place_rotate(_DummyCallback(str(rotate_cb)))  # type: ignore[arg-type]
        rotate_screen = render_calls[-1]
        _assert("Новий код згенеровано" in rotate_screen["text"], f"unexpected rotate text: {rotate_screen}")
        token_2 = _extract_token(str(rotate_screen["text"]))
        _assert(token_2 != token_1, f"token did not rotate: {token_1} == {token_2}")

        # ---- Generate branch: gen menu -> service -> place rotate
        await ah.cb_biz_tok_gen(_DummyCallback(ah.CB_BIZ_TOK_GEN))  # type: ignore[arg-type]
        gen_menu_screen = render_calls[-1]
        _assert("Згенерувати коди" in gen_menu_screen["text"], f"unexpected gen menu text: {gen_menu_screen}")

        await ah.cb_biz_tokg_service_page(_DummyCallback(f"{ah.CB_BIZ_TOKG_SVC_PAGE_PREFIX}0"))  # type: ignore[arg-type]
        gen_services_screen = render_calls[-1]
        _assert("Оберіть категорію" in gen_services_screen["text"], f"unexpected gen services text: {gen_services_screen}")
        gen_svc_pick_cb = _find_callback(gen_services_screen["reply_markup"], ah.CB_BIZ_TOKG_SVC_PICK_PREFIX)
        _assert(gen_svc_pick_cb is not None, f"gen service pick callback not found; screen={gen_services_screen}")
        _assert(
            f"{ah.CB_BIZ_TOKG_SVC_PICK_PREFIX}{service_id}|" in str(gen_svc_pick_cb),
            f"gen service callback mismatch: expected service_id={service_id}, got={gen_svc_pick_cb}",
        )

        await ah.cb_biz_tokg_service_pick(_DummyCallback(str(gen_svc_pick_cb)))  # type: ignore[arg-type]
        gen_places_screen = render_calls[-1]
        _assert("Оберіть заклад" in gen_places_screen["text"], f"unexpected gen places text: {gen_places_screen}")
        gen_rotate_cb = _find_callback(gen_places_screen["reply_markup"], ah.CB_BIZ_TOKG_PLACE_ROTATE_PREFIX)
        _assert(gen_rotate_cb is not None, f"gen rotate callback not found; screen={gen_places_screen}")
        _assert(
            f"{ah.CB_BIZ_TOKG_PLACE_ROTATE_PREFIX}{place_id}|" in str(gen_rotate_cb),
            f"gen rotate callback mismatch: expected place_id={place_id}, got={gen_rotate_cb}",
        )

        await ah.cb_biz_tokg_place_rotate(_DummyCallback(str(gen_rotate_cb)))  # type: ignore[arg-type]
        gen_rotate_screen = render_calls[-1]
        _assert("Новий код згенеровано" in gen_rotate_screen["text"], f"unexpected gen rotate text: {gen_rotate_screen}")
        token_3 = _extract_token(str(gen_rotate_screen["text"]))
        _assert(token_3 != token_2, f"token did not rotate in gen branch: {token_2} == {token_3}")
    finally:
        ah.render = original_render

    # Ensure no admin guard or data errors were raised via alert answers.
    alert_errors = []
    for args, kwargs in answer_calls:
        text = str(args[0]) if args else ""
        if bool(kwargs.get("show_alert")) and text.startswith("❌"):
            alert_errors.append(text)
    _assert(not alert_errors, f"unexpected alert errors: {alert_errors}")

    # DB-level validation for token statuses after rotation.
    conn = sqlite3.connect(db_path)
    try:
        row_1 = conn.execute(
            "SELECT token, status FROM business_claim_tokens WHERE token = ? LIMIT 1",
            (token_1,),
        ).fetchone()
        row_2 = conn.execute(
            "SELECT token, status FROM business_claim_tokens WHERE token = ? LIMIT 1",
            (token_2,),
        ).fetchone()
        row_3 = conn.execute(
            "SELECT token, status FROM business_claim_tokens WHERE token = ? LIMIT 1",
            (token_3,),
        ).fetchone()
        _assert(row_1 is not None, f"first token not found in DB: {token_1}")
        _assert(row_2 is not None, f"rotated token not found in DB: {token_2}")
        _assert(row_3 is not None, f"gen-rotated token not found in DB: {token_3}")
        _assert(str(row_1[1]) == "revoked", f"first token must be revoked after rotate: {row_1}")
        _assert(str(row_2[1]) == "revoked", f"second token must be revoked after gen rotate: {row_2}")
        _assert(str(row_3[1]) == "active", f"third token must be active: {row_3}")
    finally:
        conn.close()


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-admin-claim-flow-"))
    admin_id = 880001
    try:
        db_path = tmpdir / "state.db"
        service_id, place_id = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ["ADMIN_IDS"] = str(admin_id)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_BOT_API_KEY", "smoke-admin-token")

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks(db_path, service_id, place_id, admin_id))
        print("OK: admin claim-tokens handler flow smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
