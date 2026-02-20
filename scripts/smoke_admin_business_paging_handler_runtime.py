#!/usr/bin/env python3
"""
Dynamic smoke test: admin business paging handlers runtime contract.

Validates in real callback handlers:
- subscriptions screen renders owner Telegram contact and page navigation.
- payments screen renders owner Telegram contact and page navigation.
- TSV exports are generated and include owner-contact columns/values.
- owner priority in UI/export resolves to approved owner for conflicting rows.
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

TOTAL_SUBS = 10
TOTAL_PAYMENTS = 13
SPECIAL_OWNER_PENDING_TG_ID = 49991
SPECIAL_OWNER_APPROVED_TG_ID = 49992


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _setup_temp_db(db_path: Path) -> tuple[int, int]:
    conn = sqlite3.connect(db_path)
    special_place_id = 0
    special_service_id = 0
    try:
        schema = SCHEMA_SQL.read_text(encoding="utf-8")
        conn.executescript(schema)

        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_admin_business_handlers__",))
        special_service_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        created_at = _now_iso()
        place_ids: list[int] = []
        for idx in range(1, TOTAL_SUBS + 1):
            conn.execute(
                """
                INSERT INTO places(
                    service_id, name, description, address, keywords,
                    is_published, is_verified, verified_tier, verified_until, business_enabled
                ) VALUES(?, ?, ?, ?, ?, 1, 0, NULL, NULL, 1)
                """,
                (
                    special_service_id,
                    f"Handler Place {idx:02d}",
                    "smoke",
                    f"Addr {idx}",
                    f"kw {idx}",
                ),
            )
            place_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            place_ids.append(place_id)
            tg_user_id = 41000 + idx

            if idx == 1:
                special_place_id = place_id
                conn.execute(
                    """
                    INSERT INTO subscribers(chat_id, username, first_name, subscribed_at)
                    VALUES(?, ?, ?, ?)
                    """,
                    (
                        SPECIAL_OWNER_PENDING_TG_ID,
                        f"user{SPECIAL_OWNER_PENDING_TG_ID}",
                        f"User{SPECIAL_OWNER_PENDING_TG_ID}",
                        created_at,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO subscribers(chat_id, username, first_name, subscribed_at)
                    VALUES(?, ?, ?, ?)
                    """,
                    (
                        SPECIAL_OWNER_APPROVED_TG_ID,
                        f"user{SPECIAL_OWNER_APPROVED_TG_ID}",
                        f"User{SPECIAL_OWNER_APPROVED_TG_ID}",
                        created_at,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO subscribers(chat_id, username, first_name, subscribed_at)
                    VALUES(?, ?, ?, ?)
                    """,
                    (tg_user_id, f"user{tg_user_id}", f"User{tg_user_id}", created_at),
                )

                conn.execute(
                    """
                    INSERT INTO business_owners(
                        place_id, tg_user_id, role, status, created_at, approved_at, approved_by
                    ) VALUES(?, ?, 'owner', 'pending', datetime(?, '+10 seconds'), NULL, NULL)
                    """,
                    (place_id, SPECIAL_OWNER_PENDING_TG_ID, created_at),
                )
                conn.execute(
                    """
                    INSERT INTO business_owners(
                        place_id, tg_user_id, role, status, created_at, approved_at, approved_by
                    ) VALUES(?, ?, 'owner', 'approved', datetime(?, '-10 seconds'), ?, ?)
                    """,
                    (place_id, SPECIAL_OWNER_APPROVED_TG_ID, created_at, created_at, 1),
                )
                conn.execute(
                    """
                    INSERT INTO business_owners(
                        place_id, tg_user_id, role, status, created_at, approved_at, approved_by
                    ) VALUES(?, ?, 'owner', 'rejected', datetime(?, '+20 seconds'), ?, ?)
                    """,
                    (place_id, tg_user_id, created_at, created_at, 1),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO subscribers(chat_id, username, first_name, subscribed_at)
                    VALUES(?, ?, ?, ?)
                    """,
                    (tg_user_id, f"user{tg_user_id}", f"User{tg_user_id}", created_at),
                )
                conn.execute(
                    """
                    INSERT INTO business_owners(
                        place_id, tg_user_id, role, status, created_at, approved_at, approved_by
                    ) VALUES(?, ?, 'owner', 'approved', ?, ?, ?)
                    """,
                    (place_id, tg_user_id, created_at, created_at, 1),
                )

            tier = "light" if idx % 2 == 0 else "free"
            status = "active" if tier != "free" else "inactive"
            conn.execute(
                """
                INSERT INTO business_subscriptions(
                    place_id, tier, status, starts_at, expires_at, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    place_id,
                    tier,
                    status,
                    created_at if status == "active" else None,
                    created_at if status == "active" else None,
                    created_at,
                    created_at,
                ),
            )

        event_types = [
            "invoice_created",
            "pre_checkout_ok",
            "payment_succeeded",
            "payment_failed",
            "payment_canceled",
            "refund",
        ]
        for idx in range(1, TOTAL_PAYMENTS + 1):
            place_id = place_ids[(idx - 1) % len(place_ids)]
            provider = "telegram_stars" if idx % 2 == 0 else "mock"
            event_type = event_types[(idx - 1) % len(event_types)]
            status = "processed"
            if event_type == "payment_failed":
                status = "failed"
            elif event_type == "payment_canceled":
                status = "canceled"
            elif event_type == "invoice_created":
                status = "new"
            conn.execute(
                """
                INSERT INTO business_payment_events(
                    place_id, provider, external_payment_id, event_type,
                    amount_stars, currency, status, raw_payload_json, created_at, processed_at
                ) VALUES(?, ?, ?, ?, ?, 'XTR', ?, '{}', ?, ?)
                """,
                (
                    place_id,
                    provider,
                    f"handler_evt_{idx:04d}",
                    event_type,
                    1000,
                    status,
                    created_at,
                    created_at,
                ),
            )

        conn.commit()
        return int(special_service_id), int(special_place_id)
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


def _extract_export_row(payload: str, *, key_prefix: str) -> str | None:
    for line in payload.splitlines():
        if line.startswith(key_prefix):
            return line
    return None


async def _run_checks(
    db_path: Path,
    *,
    admin_id: int,
    special_place_id: int,
) -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    import admin.handlers as ah  # noqa: WPS433

    if int(admin_id) not in set(getattr(ah.CFG, "admin_ids", [])):
        ah.CFG.admin_ids.append(int(admin_id))
    ah.business_service.admin_ids.add(int(admin_id))

    render_calls: list[dict] = []
    alert_calls: list[tuple[tuple, dict]] = []
    exported_docs: list[dict] = []

    class _FakeBufferedInputFile:
        def __init__(self, payload: bytes, filename: str) -> None:
            self.data = bytes(payload)
            self.filename = str(filename)

    class _DummyMessage:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(id=int(admin_id))
            self.message_id = 77
            self.bot = SimpleNamespace()

        async def answer_document(self, *, document, caption: str | None = None, **_kwargs) -> None:
            exported_docs.append(
                {
                    "filename": str(getattr(document, "filename", "")),
                    "payload": bytes(getattr(document, "data", b"")),
                    "caption": str(caption or ""),
                }
            )

    class _DummyCallback:
        def __init__(self, data: str) -> None:
            self.data = str(data)
            self.from_user = SimpleNamespace(id=int(admin_id), username="admin_smoke")
            self.bot = SimpleNamespace(send_document=self._send_document_fallback)
            self.message = _DummyMessage()

        async def answer(self, *args, **kwargs) -> None:
            alert_calls.append((args, kwargs))

        async def _send_document_fallback(self, *, document, caption: str | None = None, **_kwargs) -> None:
            exported_docs.append(
                {
                    "filename": str(getattr(document, "filename", "")),
                    "payload": bytes(getattr(document, "data", b"")),
                    "caption": str(caption or ""),
                }
            )

    original_render = ah.render
    original_buffered_input_file = ah.BufferedInputFile

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

    ah.render = _fake_render
    ah.BufferedInputFile = _FakeBufferedInputFile  # type: ignore[assignment]
    try:
        # Subscriptions page 1.
        await ah.cb_business_subscriptions(_DummyCallback(ah.CB_BIZ_SUBS))  # type: ignore[arg-type]
        _assert(render_calls, "subscriptions handler did not render")
        subs_page_0 = render_calls[-1]
        _assert("💳 <b>Підписки бізнесів</b>" in subs_page_0["text"], f"unexpected subs text: {subs_page_0}")
        _assert(
            f"tg://user?id={SPECIAL_OWNER_APPROVED_TG_ID}" in subs_page_0["text"],
            f"approved owner contact missing in subscriptions screen: {subs_page_0['text']}",
        )
        _assert(
            f"tg://user?id={SPECIAL_OWNER_PENDING_TG_ID}" not in subs_page_0["text"],
            "pending owner must not win subscriptions owner-priority",
        )
        subs_next_cb = _find_callback(subs_page_0["reply_markup"], ah.CB_BIZ_SUBS_PAGE_PREFIX)
        _assert(subs_next_cb is not None, "subscriptions pagination callback missing")
        _assert(str(subs_next_cb).endswith("1"), f"unexpected subscriptions next callback: {subs_next_cb}")

        # Subscriptions page 2.
        await ah.cb_business_subscriptions_page(_DummyCallback(str(subs_next_cb)))  # type: ignore[arg-type]
        subs_page_1 = render_calls[-1]
        _assert("💳 <b>Підписки бізнесів</b>" in subs_page_1["text"], f"unexpected subs page-1 text: {subs_page_1}")
        _assert("Записів: <b>10</b>" in subs_page_1["text"], f"unexpected subs total on page-1: {subs_page_1['text']}")

        # Subscriptions export.
        await ah.cb_business_subscriptions_export(_DummyCallback(ah.CB_BIZ_SUBS_EXPORT))  # type: ignore[arg-type]
        _assert(exported_docs, "subscriptions export did not send document")
        subs_export = exported_docs[-1]
        _assert(
            subs_export["filename"] == "business_subscriptions.tsv",
            f"subscriptions export filename mismatch: {subs_export}",
        )
        subs_payload = subs_export["payload"].decode("utf-8")
        _assert(
            "owner_tg_user_id\towner_username\towner_first_name\towner_status" in subs_payload,
            "subscriptions export header missing owner columns",
        )
        subs_special_row = _extract_export_row(subs_payload, key_prefix=f"{special_place_id}\t")
        _assert(subs_special_row is not None, f"special place row missing in subscriptions export: {special_place_id}")
        _assert(
            f"\t{SPECIAL_OWNER_APPROVED_TG_ID}\tuser{SPECIAL_OWNER_APPROVED_TG_ID}\tUser{SPECIAL_OWNER_APPROVED_TG_ID}\tapproved\t"
            in str(subs_special_row),
            f"subscriptions export owner-priority mismatch row={subs_special_row}",
        )

        # Payments page 1.
        await ah.cb_business_payments(_DummyCallback(ah.CB_BIZ_PAYMENTS))  # type: ignore[arg-type]
        payments_page_0 = render_calls[-1]
        _assert("💸 <b>Платіжні події</b>" in payments_page_0["text"], f"unexpected payments text: {payments_page_0}")
        _assert(
            f"tg://user?id={SPECIAL_OWNER_APPROVED_TG_ID}" in payments_page_0["text"],
            f"approved owner contact missing in payments screen: {payments_page_0['text']}",
        )
        _assert(
            f"tg://user?id={SPECIAL_OWNER_PENDING_TG_ID}" not in payments_page_0["text"],
            "pending owner must not win payments owner-priority",
        )
        pay_next_cb = _find_callback(payments_page_0["reply_markup"], ah.CB_BIZ_PAYMENTS_PAGE_PREFIX)
        _assert(pay_next_cb is not None, "payments pagination callback missing")
        _assert(str(pay_next_cb).endswith("1"), f"unexpected payments next callback: {pay_next_cb}")

        # Payments export.
        await ah.cb_business_payments_export(_DummyCallback(ah.CB_BIZ_PAYMENTS_EXPORT))  # type: ignore[arg-type]
        _assert(len(exported_docs) >= 2, "payments export did not send document")
        pay_export = exported_docs[-1]
        _assert(pay_export["filename"] == "business_payments.tsv", f"payments export filename mismatch: {pay_export}")
        pay_payload = pay_export["payload"].decode("utf-8")
        _assert(
            "owner_tg_user_id\towner_username\towner_first_name\towner_status" in pay_payload,
            "payments export header missing owner columns",
        )
        _assert(
            f"\t{SPECIAL_OWNER_APPROVED_TG_ID}\tuser{SPECIAL_OWNER_APPROVED_TG_ID}\tUser{SPECIAL_OWNER_APPROVED_TG_ID}\tapproved\t"
            in pay_payload,
            "payments export owner-priority mismatch for special place",
        )
    finally:
        ah.render = original_render
        ah.BufferedInputFile = original_buffered_input_file  # type: ignore[assignment]

    # Ensure handlers didn't raise admin/data alerts.
    errors = []
    for args, kwargs in alert_calls:
        text = str(args[0]) if args else ""
        if bool(kwargs.get("show_alert")) and text.startswith("❌"):
            errors.append(text)
    _assert(not errors, f"unexpected alert errors: {errors}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-admin-biz-handler-paging-"))
    admin_id = 780001
    try:
        db_path = tmpdir / "state.db"
        _service_id, special_place_id = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ["ADMIN_IDS"] = str(admin_id)
        os.environ["BUSINESS_MODE"] = "1"
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_BOT_API_KEY", "smoke-admin-token")

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(
            _run_checks(
                db_path,
                admin_id=int(admin_id),
                special_place_id=int(special_place_id),
            )
        )
        print("OK: admin business paging handlers runtime smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
