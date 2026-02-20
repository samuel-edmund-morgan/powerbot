#!/usr/bin/env python3
"""
Runtime smoke: admin offers-digest worker handler contract.

Checks:
- `_handle_offers_digest` sends only to eligible recipients (opt-in + no quiet + no rate-limit).
- `admin_jobs.progress_current/progress_total` are updated to sent/total.
- `offers_digest_last_sent_at:<chat_id>` is written for successful recipients.
- Subsequent job respects rate-limit and can result in zero recipients.

Run in container:
  docker compose exec -T powerbot python - < scripts/smoke_offers_digest_worker_runtime.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path


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
    raise FileNotFoundError("Cannot locate repo root")


REPO_ROOT = _resolve_repo_root()
SCHEMA_SQL = REPO_ROOT / "schema.sql"


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _setup_temp_db(db_path: Path) -> None:
    now = datetime.now()
    now_hour = now.hour
    quiet_start = (now_hour - 1) % 24
    quiet_end = (now_hour + 1) % 24

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        subscribers = [
            (2001, None, None, "u2001", "User2001"),  # eligible
            (2002, None, None, "u2002", "User2002"),  # opt-out
            (2003, quiet_start, quiet_end, "u2003", "User2003"),  # quiet now
            (2004, None, None, "u2004", "User2004"),  # recent digest -> rate-limited
            (2005, None, None, "u2005", "User2005"),  # old digest -> eligible
        ]
        conn.executemany(
            """
            INSERT INTO subscribers(chat_id, quiet_start, quiet_end, username, first_name, subscribed_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            [(cid, qs, qe, uname, fname, now.isoformat()) for cid, qs, qe, uname, fname in subscribers],
        )
        kv_rows = [
            ("offers_digest_enabled:2001", "1"),
            ("offers_digest_enabled:2002", "0"),
            ("offers_digest_enabled:2003", "true"),
            ("offers_digest_enabled:2004", "yes"),
            ("offers_digest_enabled:2005", "on"),
            ("offers_digest_last_sent_at:2004", (now - timedelta(hours=2)).isoformat()),
            ("offers_digest_last_sent_at:2005", (now - timedelta(hours=48)).isoformat()),
        ]
        conn.executemany("INSERT INTO kv(k,v) VALUES(?,?)", kv_rows)
        conn.commit()
    finally:
        conn.close()


async def _run_checks() -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    from aiogram import Bot  # noqa: WPS433
    import admin_jobs_worker as worker  # noqa: WPS433
    from database import create_admin_job, db_get, get_admin_job  # noqa: WPS433

    sent_chat_ids: list[int] = []

    original_send_message = Bot.send_message
    original_broadcast_messages = worker.broadcast_messages

    async def _fake_send_message(self, chat_id: int, text: str, **_kwargs):
        sent_chat_ids.append(int(chat_id))
        return types.SimpleNamespace(message_id=1, chat=types.SimpleNamespace(id=int(chat_id)), text=text)

    async def _fake_broadcast_messages(subscribers, send_func):
        for chat_id in subscribers:
            await send_func(int(chat_id))

    Bot.send_message = _fake_send_message  # type: ignore[assignment]
    worker.broadcast_messages = _fake_broadcast_messages  # type: ignore[assignment]
    try:
        payload = {
            "text": "Тестовий дайджест партнерів",
            "prefix": "📬 ",
            "min_interval_hours": 24,
        }

        job_id_1 = await create_admin_job("offers_digest", payload, created_by=1)
        sent_1, total_1 = await worker._handle_offers_digest(  # noqa: SLF001
            bot=types.SimpleNamespace(),
            job={"id": int(job_id_1), "payload": payload},
        )
        _assert((sent_1, total_1) == (2, 2), f"unexpected send/total for first digest job: {(sent_1, total_1)}")
        _assert(sorted(sent_chat_ids) == [2001, 2005], f"unexpected recipient list: {sorted(sent_chat_ids)}")

        state_1 = await get_admin_job(int(job_id_1))
        _assert(state_1 is not None, "first job state is missing")
        _assert(int(state_1.get("progress_current") or -1) == 2, f"unexpected progress_current: {state_1}")
        _assert(int(state_1.get("progress_total") or -1) == 2, f"unexpected progress_total: {state_1}")

        for chat_id in (2001, 2005):
            raw = str((await db_get(f"offers_digest_last_sent_at:{chat_id}")) or "").strip()
            _assert(bool(raw), f"missing offers_digest_last_sent_at for chat_id={chat_id}")
            try:
                datetime.fromisoformat(raw)
            except Exception as exc:
                raise AssertionError(f"invalid ISO timestamp for chat_id={chat_id}: {raw}") from exc

        # Second run immediately after first one should find no eligible recipients.
        sent_chat_ids.clear()
        job_id_2 = await create_admin_job("offers_digest", payload, created_by=1)
        sent_2, total_2 = await worker._handle_offers_digest(  # noqa: SLF001
            bot=types.SimpleNamespace(),
            job={"id": int(job_id_2), "payload": payload},
        )
        _assert((sent_2, total_2) == (0, 0), f"unexpected send/total for second digest job: {(sent_2, total_2)}")
        _assert(sent_chat_ids == [], f"no messages expected on second run, got: {sent_chat_ids}")

        state_2 = await get_admin_job(int(job_id_2))
        _assert(state_2 is not None, "second job state is missing")
        _assert(int(state_2.get("progress_current") or -1) == 0, f"unexpected progress_current: {state_2}")
        _assert(int(state_2.get("progress_total") or -1) == 0, f"unexpected progress_total: {state_2}")
    finally:
        Bot.send_message = original_send_message  # type: ignore[assignment]
        worker.broadcast_messages = original_broadcast_messages  # type: ignore[assignment]


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-offers-worker-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "1")

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: offers digest worker runtime smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
