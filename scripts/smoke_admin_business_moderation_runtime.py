#!/usr/bin/env python3
"""
Dynamic smoke test: admin business moderation handler runtime contract.

Validates callback-handler flow:
- open moderation queue in adminbot
- jump to specific pending request
- approve pending owner request for a newly created place
- approve pending owner request from claim-token flow
- reject pending owner request for another newly created place
- ensure resident visibility gate follows publish state after moderation
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


def _setup_temp_db(db_path: Path) -> tuple[int, int]:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_admin_mod_runtime__",))
        service_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        claim_place_name = f"Claim Existing {int(time.time())}"
        conn.execute(
            """
            INSERT INTO places(service_id, name, description, address, keywords, is_published, business_enabled)
            VALUES(?, ?, ?, ?, ?, 1, 1)
            """,
            (service_id, claim_place_name, "smoke", "claim addr", "claim"),
        )
        claim_place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.commit()
        return service_id, claim_place_id
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


async def _run_checks(db_path: Path, *, service_id: int, claim_place_id: int, admin_id: int) -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    import admin.handlers as ah  # noqa: WPS433
    from database import get_places_by_service_with_likes  # noqa: WPS433

    if int(admin_id) not in set(getattr(ah.CFG, "admin_ids", [])):
        ah.CFG.admin_ids.append(int(admin_id))
    ah.business_service.admin_ids.add(int(admin_id))

    owner_approve_tg_id = int(f"97{int(time.time()) % 100000000:08d}")
    owner_reject_tg_id = owner_approve_tg_id + 1
    owner_claim_tg_id = owner_approve_tg_id + 2

    created_approve = await ah.business_service.register_new_business(
        tg_user_id=owner_approve_tg_id,
        service_id=int(service_id),
        place_name=f"Moderation Approve Place {int(time.time())}",
        description="smoke",
        address="approve addr",
    )
    created_reject = await ah.business_service.register_new_business(
        tg_user_id=owner_reject_tg_id,
        service_id=int(service_id),
        place_name=f"Moderation Reject Place {int(time.time())}",
        description="smoke",
        address="reject addr",
    )

    owner_approve = created_approve.get("owner") or {}
    owner_reject = created_reject.get("owner") or {}
    place_approve = created_approve.get("place") or {}
    place_reject = created_reject.get("place") or {}

    owner_approve_id = int(owner_approve.get("id") or 0)
    owner_reject_id = int(owner_reject.get("id") or 0)
    place_approve_id = int(place_approve.get("id") or 0)
    place_reject_id = int(place_reject.get("id") or 0)
    _assert(owner_approve_id > 0 and place_approve_id > 0, f"invalid approve fixture: {created_approve}")
    _assert(owner_reject_id > 0 and place_reject_id > 0, f"invalid reject fixture: {created_reject}")

    token_bundle = await ah.business_service.get_or_create_active_claim_token_for_place(int(admin_id), int(claim_place_id))
    token_row = token_bundle.get("token_row") or {}
    token = str(token_row.get("token") or "")
    _assert(token, "claim token must be generated")
    claimed = await ah.business_service.claim_business_by_token(int(owner_claim_tg_id), token)
    owner_claim = claimed.get("owner") or {}
    owner_claim_id = int(owner_claim.get("id") or 0)
    _assert(owner_claim_id > 0, f"invalid claim fixture: {claimed}")

    # Before moderation: only existing published place must be visible to residents.
    visible_before = await get_places_by_service_with_likes(int(service_id))
    visible_before_ids = {int(row.get("id") or 0) for row in visible_before}
    _assert(int(claim_place_id) in visible_before_ids, "existing claimed place must be visible before approve")
    _assert(int(place_approve_id) not in visible_before_ids, "new pending place must be hidden before approve")
    _assert(int(place_reject_id) not in visible_before_ids, "new pending place must be hidden before reject")

    render_calls: list[dict] = []
    answer_calls: list[tuple[tuple, dict]] = []
    owner_notifications: list[tuple[int, str]] = []

    class _DummyMessage:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(id=int(admin_id))
            self.message_id = 77
            self.bot = SimpleNamespace()

    class _DummyCallback:
        def __init__(self, data: str) -> None:
            self.data = str(data)
            self.from_user = SimpleNamespace(id=int(admin_id), username="admin_smoke")
            self.bot = SimpleNamespace()
            self.message = _DummyMessage()

        async def answer(self, *args, **kwargs) -> None:
            answer_calls.append((args, kwargs))

    original_render = ah.render
    original_notify_owner = ah._notify_owner_via_business_bot

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

    async def _fake_notify_owner(owner_tg_user_id: int, text: str) -> None:
        owner_notifications.append((int(owner_tg_user_id), str(text)))

    ah.render = _fake_render
    ah._notify_owner_via_business_bot = _fake_notify_owner
    try:
        await ah.cb_business_moderation(_DummyCallback(ah.CB_BIZ_MOD))  # type: ignore[arg-type]
        _assert(render_calls, "moderation queue did not render")
        _assert("🛡 <b>Модерація</b>" in render_calls[-1]["text"], f"unexpected moderation text: {render_calls[-1]}")

        async def _jump(owner_id: int) -> None:
            await ah.cb_business_moderation_jump(_DummyCallback(f"{ah.CB_BIZ_MOD_JUMP_PREFIX}{int(owner_id)}"))  # type: ignore[arg-type]
            _assert(render_calls, f"jump for owner_id={owner_id} produced no render")
            _assert(
                f"<code>{int(owner_id)}</code>" in render_calls[-1]["text"],
                f"jump did not focus owner_id={owner_id}; screen={render_calls[-1]}",
            )

        async def _approve(owner_id: int) -> None:
            await _jump(owner_id)
            approve_cb = _find_callback(render_calls[-1]["reply_markup"], f"{ah.CB_BIZ_MOD_APPROVE_PREFIX}{int(owner_id)}|")
            _assert(approve_cb is not None, f"approve callback not found for owner_id={owner_id}")
            await ah.cb_business_moderation_approve(_DummyCallback(str(approve_cb)))  # type: ignore[arg-type]

        async def _reject(owner_id: int) -> None:
            await _jump(owner_id)
            reject_cb = _find_callback(render_calls[-1]["reply_markup"], f"{ah.CB_BIZ_MOD_REJECT_PREFIX}{int(owner_id)}|")
            _assert(reject_cb is not None, f"reject callback not found for owner_id={owner_id}")
            await ah.cb_business_moderation_reject(_DummyCallback(str(reject_cb)))  # type: ignore[arg-type]

        await _approve(int(owner_approve_id))
        updated_approve_owner = await ah.business_service.repository.get_owner_request(int(owner_approve_id))
        _assert(updated_approve_owner is not None, "approved owner row missing")
        _assert(str(updated_approve_owner.get("status") or "") == "approved", f"approve status mismatch: {updated_approve_owner}")

        updated_approve_place = await ah.business_service.repository.get_place(int(place_approve_id))
        _assert(updated_approve_place is not None, "approved place row missing")
        _assert(int(updated_approve_place.get("is_published") or 0) == 1, f"approved place must become published: {updated_approve_place}")
        _assert(int(updated_approve_place.get("business_enabled") or 0) == 1, f"approved place must become business_enabled: {updated_approve_place}")

        await _approve(int(owner_claim_id))
        updated_claim_owner = await ah.business_service.repository.get_owner_request(int(owner_claim_id))
        _assert(updated_claim_owner is not None, "approved claim-owner row missing")
        _assert(str(updated_claim_owner.get("status") or "") == "approved", f"claim approve status mismatch: {updated_claim_owner}")

        await _reject(int(owner_reject_id))
        updated_reject_owner = await ah.business_service.repository.get_owner_request(int(owner_reject_id))
        _assert(updated_reject_owner is not None, "rejected owner row missing")
        _assert(str(updated_reject_owner.get("status") or "") == "rejected", f"reject status mismatch: {updated_reject_owner}")

        updated_reject_place = await ah.business_service.repository.get_place(int(place_reject_id))
        _assert(updated_reject_place is not None, "rejected place row missing")
        _assert(
            int(updated_reject_place.get("is_published") or 0) == 0,
            f"rejected new place must stay unpublished: {updated_reject_place}",
        )

        pending_rows = await ah.business_service.list_pending_owner_requests(int(admin_id))
        _assert(not pending_rows, f"pending moderation queue must be empty, got: {pending_rows}")

        # Queue empty screen should be rendered after final moderation action.
        _assert(any("Черга модерації порожня" in call["text"] for call in render_calls), "empty moderation screen was not rendered")
    finally:
        ah.render = original_render
        ah._notify_owner_via_business_bot = original_notify_owner

    # Resident visibility check after moderation actions.
    visible_after = await get_places_by_service_with_likes(int(service_id))
    visible_after_ids = {int(row.get("id") or 0) for row in visible_after}
    _assert(int(place_approve_id) in visible_after_ids, "approved new place must be visible for residents")
    _assert(int(claim_place_id) in visible_after_ids, "approved claimed place must stay visible")
    _assert(int(place_reject_id) not in visible_after_ids, "rejected new place must stay hidden for residents")

    # Owner notifications must be emitted for all moderation terminal actions.
    notified_ids = {item[0] for item in owner_notifications}
    _assert(int(owner_approve_tg_id) in notified_ids, "approved owner must get notification")
    _assert(int(owner_claim_tg_id) in notified_ids, "approved claim-owner must get notification")
    _assert(int(owner_reject_tg_id) in notified_ids, "rejected owner must get notification")

    # No unexpected alert-errors.
    alert_errors: list[str] = []
    for args, kwargs in answer_calls:
        text = str(args[0]) if args else ""
        if bool(kwargs.get("show_alert")) and text.startswith("❌"):
            alert_errors.append(text)
    _assert(not alert_errors, f"unexpected moderation alert errors: {alert_errors}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-admin-mod-runtime-"))
    admin_id = 980001
    try:
        db_path = tmpdir / "state.db"
        service_id, claim_place_id = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ["ADMIN_IDS"] = str(admin_id)
        os.environ.setdefault("BOT_TOKEN", "smoke-main-token")
        os.environ.setdefault("ADMIN_BOT_API_KEY", "smoke-admin-token")
        os.environ.setdefault("BUSINESS_BOT_API_KEY", "smoke-business-token")
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(
            _run_checks(
                db_path,
                service_id=int(service_id),
                claim_place_id=int(claim_place_id),
                admin_id=int(admin_id),
            )
        )
        print("OK: admin business moderation runtime smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
