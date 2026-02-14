"""Lightweight SQLite lock observability.

We use SQLite across multiple bot processes (powerbot/adminbot/businessbot). Even with WAL and
busy_timeout, lock contention can still happen on concurrent writes. To make this visible in
production, we log lock events to a JSONL file under the mounted /data directory:

  /data/logs/locks.log

This maps to:
  /opt/powerbot/logs/locks.log (prod)
  /opt/powerbot-test/logs/locks.log (test)

The logger is best-effort: it must never crash application code.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK_LOG_PATH: str | None = None
_LOCK_LOG_PATH_INITIALIZED = False


def _touch_log_file(path: str) -> None:
    try:
        log_path = Path(path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # touch/create empty file so ops can "tail" even before first lock event
        log_path.open("a", encoding="utf-8").close()
    except Exception:
        return


def _resolve_lock_log_path() -> str | None:
    """Resolve lock log path once per process."""
    global _LOCK_LOG_PATH_INITIALIZED, _LOCK_LOG_PATH
    if _LOCK_LOG_PATH_INITIALIZED:
        return _LOCK_LOG_PATH
    _LOCK_LOG_PATH_INITIALIZED = True

    explicit = (os.getenv("SQLITE_LOCK_LOG_PATH") or "").strip().strip('"').strip("'")
    if explicit:
        _LOCK_LOG_PATH = explicit
        _touch_log_file(_LOCK_LOG_PATH)
        return _LOCK_LOG_PATH

    # Default for docker-compose environments where DB is mounted under /data.
    db_path = (os.getenv("DB_PATH") or "").strip().strip('"').strip("'")
    if db_path.startswith("/data/"):
        _LOCK_LOG_PATH = "/data/logs/locks.log"
        _touch_log_file(_LOCK_LOG_PATH)
        return _LOCK_LOG_PATH

    _LOCK_LOG_PATH = None
    return None


def log_sqlite_lock_event(
    *,
    where: str,
    exc: BaseException,
    attempt: int,
    retries: int,
    delay_sec: float | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append a JSONL entry about lock contention.

    attempt: 1-based attempt number for readability (1..retries+1).
    """
    path = _resolve_lock_log_path()
    if not path:
        return

    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "where": str(where or ""),
        "attempt": int(attempt),
        "retries": int(retries),
        "error": str(exc),
        "pid": os.getpid(),
    }
    entrypoint = (os.getenv("APP_ENTRYPOINT") or "").strip().strip('"').strip("'")
    if entrypoint:
        payload["entrypoint"] = entrypoint
    db_path = (os.getenv("DB_PATH") or "").strip().strip('"').strip("'")
    if db_path:
        payload["db_path"] = db_path
    if delay_sec is not None:
        try:
            payload["delay_sec"] = float(delay_sec)
        except Exception:
            payload["delay_sec"] = str(delay_sec)
    if extra:
        for k, v in extra.items():
            if k in payload:
                continue
            payload[k] = v

    try:
        log_path = Path(path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        # Never fail application flow on lock logging.
        return


# Best-effort: create the log file early in docker-compose environments so it exists even
# before any lock contention happens.
try:
    _resolve_lock_log_path()
except Exception:
    pass
