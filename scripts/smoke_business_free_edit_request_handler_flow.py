#!/usr/bin/env python3
"""
Dynamic smoke test: businessbot free edit-request handler flow.

Checks:
- free owner can open "Запропонувати правку" flow
- text submit creates `place_reports` row
- submit enqueues `admin_place_report_alert` job
- flow ends with success note (no "stuck" FSM state)
"""

from __future__ import annotations

import asyncio
import json
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


def _setup_temp_db(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_free_edit__",))
        conn.commit()
        return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    finally:
        conn.close()


class _DummyState:
    def __init__(self) -> None:
        self._data: dict[str, object] = {}
        self.cleared = False

    async def set_state(self, _state) -> None:
        return None

    async def update_data(self, **kwargs) -> None:
        self._data.update(kwargs)

    async def get_data(self) -> dict[str, object]:
        return dict(self._data)

    async def clear(self) -> None:
        self._data.clear()
        self.cleared = True


async def _run_checks(db_path: Path, service_id: int) -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    import business.handlers as bh  # noqa: WPS433

    admin_tg_id = 910001
    owner_tg_id = 920000 + int(time.time()) % 10000
    bh.cabinet_service.admin_ids.add(admin_tg_id)

    created = await bh.cabinet_service.register_new_business(
        tg_user_id=owner_tg_id,
        service_id=int(service_id),
        place_name=f"Free Edit Smoke {int(time.time())}",
        description="smoke",
        address="addr",
    )
    owner = created.get("owner") or {}
    place = created.get("place") or {}
    owner_id = int(owner.get("id") or 0)
    place_id = int(place.get("id") or 0)
    _assert(owner_id > 0 and place_id > 0, f"invalid created objects: {created}")

    await bh.cabinet_service.approve_owner_request(admin_tg_id, owner_id)

    # Monkeypatch UI helpers to avoid Telegram API calls and capture behavior.
    original_ui_render = bh.ui_render
    original_render_place_card_updated = bh.render_place_card_updated
    original_try_delete_user_message = bh.try_delete_user_message
    ui_calls: list[dict] = []
    card_calls: list[dict] = []
    answer_calls: list[tuple[tuple, dict]] = []

    async def _fake_ui_render(bot, *, chat_id, prefer_message_id=None, text="", reply_markup=None, **kwargs):
        ui_calls.append(
            {
                "chat_id": int(chat_id),
                "prefer_message_id": int(prefer_message_id or 0),
                "text": str(text),
                "reply_markup": reply_markup,
                "kwargs": kwargs,
            }
        )
        return SimpleNamespace(message_id=prefer_message_id or 0)

    async def _fake_render_place_card_updated(message, *, place_id, note_text: str | None = None):
        card_calls.append(
            {
                "chat_id": int(message.chat.id),
                "place_id": int(place_id),
                "note_text": str(note_text or ""),
            }
        )
        return None

    async def _fake_try_delete_user_message(_message):
        return None

    class _DummyCallback:
        def __init__(self, user_id: int, place_id: int):
            self.data = f"{bh.CB_FREE_EDIT_REQUEST_PREFIX}{place_id}"
            self.from_user = SimpleNamespace(id=int(user_id), username="owner_smoke", first_name="Owner", last_name=None)
            self.message = SimpleNamespace(
                chat=SimpleNamespace(id=930001),
                message_id=77,
                bot=SimpleNamespace(),
            )

        async def answer(self, *args, **kwargs):
            answer_calls.append((args, kwargs))

    class _DummyMessage:
        def __init__(self, user_id: int, text: str):
            self.text = str(text)
            self.chat = SimpleNamespace(id=930001)
            self.bot = SimpleNamespace()
            self.from_user = SimpleNamespace(id=int(user_id), username="owner_smoke", first_name="Owner", last_name=None)

    bh.ui_render = _fake_ui_render
    bh.render_place_card_updated = _fake_render_place_card_updated
    bh.try_delete_user_message = _fake_try_delete_user_message
    try:
        state = _DummyState()

        # Step 1: open flow from inline callback.
        cb = _DummyCallback(owner_tg_id, place_id)
        await bh.cb_free_edit_request_start(cb, state)  # type: ignore[arg-type]

        _assert(answer_calls, "callback must be answered")
        data_after_start = await state.get_data()
        _assert(int(data_after_start.get("free_edit_request_place_id") or 0) == place_id, "FSM place_id not set")
        _assert(ui_calls, "start flow must render request screen")
        _assert("Запропонувати правку" in str(ui_calls[0].get("text") or ""), f"unexpected start UI: {ui_calls[0]}")

        # Step 2: submit text report.
        msg = _DummyMessage(owner_tg_id, "Оновіть, будь ласка, опис закладу.")
        await bh.msg_free_edit_request_submit(msg, state)  # type: ignore[arg-type]

        _assert(state.cleared, "state must be cleared after successful submit")
        _assert(card_calls, "success flow must render updated place card")
        _assert(
            "Передали правку адміну" in str(card_calls[-1].get("note_text") or ""),
            f"unexpected success note: {card_calls[-1]}",
        )
    finally:
        bh.ui_render = original_ui_render
        bh.render_place_card_updated = original_render_place_card_updated
        bh.try_delete_user_message = original_try_delete_user_message

    # Validate persisted report and queued alert job.
    conn = sqlite3.connect(db_path)
    try:
        report_row = conn.execute(
            "SELECT id, place_id, report_text, status FROM place_reports ORDER BY id DESC LIMIT 1"
        ).fetchone()
        _assert(report_row is not None, "place report was not created")
        report_id = int(report_row[0])
        _assert(int(report_row[1]) == place_id, f"report place mismatch: {report_row}")
        _assert("Оновіть" in str(report_row[2] or ""), f"report text mismatch: {report_row}")
        _assert(str(report_row[3] or "") == "pending", f"report status mismatch: {report_row}")

        job_row = conn.execute(
            "SELECT kind, payload_json, status FROM admin_jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        _assert(job_row is not None, "admin alert job was not created")
        _assert(str(job_row[0]) == "admin_place_report_alert", f"job kind mismatch: {job_row}")
        payload = json.loads(str(job_row[1] or "{}"))
        _assert(int(payload.get("report_id") or 0) == report_id, f"job report_id mismatch: {payload}")
        _assert(int(payload.get("place_id") or 0) == place_id, f"job place_id mismatch: {payload}")
        _assert(int(payload.get("reporter_tg_user_id") or 0) == owner_tg_id, f"job reporter mismatch: {payload}")
    finally:
        conn.close()


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-free-edit-handler-"))
    try:
        db_path = tmpdir / "state.db"
        service_id = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "910001")
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks(db_path, service_id))
        print("OK: business free edit-request handler flow smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
