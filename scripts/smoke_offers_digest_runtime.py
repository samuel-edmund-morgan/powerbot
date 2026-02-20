#!/usr/bin/env python3
"""
Runtime smoke: partner offers digest eligibility and sent-mark contract.

Checks:
- recipients are selected only when:
  - opted-in (`offers_digest_enabled`)
  - outside quiet hours
  - not rate-limited by `offers_digest_last_sent_at`
- `mark_offers_digest_sent` writes `offers_digest_last_sent_at` for successful recipients
  and they become ineligible until interval passes.

Run in container:
  docker compose exec -T powerbot python - < scripts/smoke_offers_digest_runtime.py
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
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))

        subscribers = [
            # Eligible baseline (opted-in, no quiet, no last_sent).
            (1001, None, None, "u1001", "User1001"),
            # Opt-out -> never eligible.
            (1002, None, None, "u1002", "User1002"),
            # Opted-in but in quiet hours at current_hour=12.
            (1003, 8, 18, "u1003", "User1003"),
            # Opted-in but rate-limited (recent send).
            (1004, None, None, "u1004", "User1004"),
            # Opted-in with old last_sent -> eligible.
            (1005, None, None, "u1005", "User1005"),
        ]
        conn.executemany(
            """
            INSERT INTO subscribers(chat_id, quiet_start, quiet_end, username, first_name, subscribed_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            [(cid, qs, qe, uname, fname, now.isoformat()) for cid, qs, qe, uname, fname in subscribers],
        )

        kv_rows = [
            ("offers_digest_enabled:1001", "1"),
            ("offers_digest_enabled:1002", "0"),
            ("offers_digest_enabled:1003", "yes"),
            ("offers_digest_enabled:1004", "true"),
            ("offers_digest_enabled:1005", "on"),
            # recent send (2h ago) -> ineligible for 24h interval
            ("offers_digest_last_sent_at:1004", (now - timedelta(hours=2)).isoformat()),
            # old send (48h ago) -> eligible for 24h interval
            ("offers_digest_last_sent_at:1005", (now - timedelta(hours=48)).isoformat()),
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

    from database import (  # noqa: WPS433
        db_get,
        get_subscribers_for_offers_digest,
        mark_offers_digest_sent,
        offers_digest_last_sent_at_key,
    )

    # Noon: user 1003 is in quiet hours (8..18) and must be excluded.
    noon_recipients = sorted(
        await get_subscribers_for_offers_digest(current_hour=12, min_interval_hours=24)
    )
    _assert(noon_recipients == [1001, 1005], f"unexpected noon recipients: {noon_recipients}")

    marked = await mark_offers_digest_sent([1001, 1005, 1005])
    _assert(marked == 2, f"unexpected marked count: {marked}")

    for chat_id in (1001, 1005):
        raw = str((await db_get(offers_digest_last_sent_at_key(chat_id))) or "").strip()
        _assert(bool(raw), f"missing last_sent_at for chat_id={chat_id}")
        # Must be ISO parseable.
        try:
            datetime.fromisoformat(raw)
        except Exception as exc:  # pragma: no cover - explicit failure surface
            raise AssertionError(f"invalid ISO datetime for chat_id={chat_id}: {raw}") from exc

    # Immediately after mark, both 1001 and 1005 become rate-limited.
    after_mark = sorted(
        await get_subscribers_for_offers_digest(current_hour=12, min_interval_hours=24)
    )
    _assert(after_mark == [], f"unexpected recipients after mark: {after_mark}")

    # Evening: user 1003 is outside quiet hours and has no last_sent -> eligible.
    evening_recipients = sorted(
        await get_subscribers_for_offers_digest(current_hour=20, min_interval_hours=24)
    )
    _assert(evening_recipients == [1003], f"unexpected evening recipients: {evening_recipients}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-offers-digest-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: offers digest runtime smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
