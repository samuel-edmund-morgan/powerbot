#!/usr/bin/env python3
"""
SQLite concurrency smoke-check (3 writers, short transactions, retry/backoff).

What it validates:
- WAL + busy_timeout allow concurrent writers without unrecovered lock errors
- write operations remain short (`BEGIN IMMEDIATE` -> writes -> `COMMIT`)
- retry/backoff resolves transient `database is locked` contention

Run:
  python3 scripts/smoke_sqlite_concurrency.py
"""

from __future__ import annotations

import multiprocessing as mp
import os
import random
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path


PROCESS_COUNT = 3
OPS_PER_PROCESS = 320
BUSY_TIMEOUT_MS = 5000
RETRY_ATTEMPTS = 8
RETRY_BASE_DELAY_SEC = 0.002


def _init_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS};")
        conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS writes_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_id INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _rollback_if_needed(conn: sqlite3.Connection) -> None:
    try:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
    except Exception:
        pass


def _worker(db_path: str, worker_id: int, ops: int, q: mp.Queue) -> None:
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS};")

    successes = 0
    lock_retries = 0
    unrecovered_lock_errors = 0
    hard_errors: list[str] = []

    try:
        for seq in range(ops):
            done = False
            for attempt in range(RETRY_ATTEMPTS):
                try:
                    # Keep transaction short: no network calls, only DB writes.
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        """
                        INSERT INTO writes_log(worker_id, seq, created_at)
                        VALUES(?, ?, strftime('%Y-%m-%dT%H:%M:%f', 'now'))
                        """,
                        (int(worker_id), int(seq)),
                    )
                    conn.execute(
                        """
                        INSERT INTO kv(k, v) VALUES(?, ?)
                        ON CONFLICT(k) DO UPDATE SET v=excluded.v
                        """,
                        (f"worker_{worker_id}_last_seq", str(seq)),
                    )
                    conn.execute("COMMIT")
                    successes += 1
                    if attempt > 0:
                        lock_retries += attempt
                    done = True
                    break
                except sqlite3.OperationalError as error:
                    _rollback_if_needed(conn)
                    if "database is locked" not in str(error).lower():
                        hard_errors.append(f"OperationalError: {error}")
                        break
                    if attempt >= RETRY_ATTEMPTS - 1:
                        unrecovered_lock_errors += 1
                        break
                    delay = RETRY_BASE_DELAY_SEC * (2**attempt) + random.uniform(0, RETRY_BASE_DELAY_SEC)
                    time.sleep(delay)
                except Exception as error:  # noqa: BLE001
                    _rollback_if_needed(conn)
                    hard_errors.append(f"{type(error).__name__}: {error}")
                    break

            if not done and not hard_errors and unrecovered_lock_errors == 0:
                # Defensive fallback if loop exits unexpectedly.
                hard_errors.append(f"write seq={seq} not completed")

            # Tiny jitter helps interleave writers naturally.
            time.sleep(random.uniform(0, 0.0015))
    finally:
        try:
            conn.close()
        except Exception:
            pass

    q.put(
        {
            "worker_id": int(worker_id),
            "successes": int(successes),
            "lock_retries": int(lock_retries),
            "unrecovered_lock_errors": int(unrecovered_lock_errors),
            "hard_errors": hard_errors,
        }
    )


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-sqlite-concurrency-"))
    try:
        db_path = tmpdir / "state.db"
        _init_db(db_path)

        ctx = mp.get_context("spawn")
        queue: mp.Queue = ctx.Queue()
        procs: list[mp.Process] = []

        for worker_id in range(1, PROCESS_COUNT + 1):
            p = ctx.Process(
                target=_worker,
                args=(str(db_path), worker_id, OPS_PER_PROCESS, queue),
                daemon=False,
            )
            p.start()
            procs.append(p)

        for p in procs:
            p.join(timeout=120)

        for p in procs:
            _assert(not p.is_alive(), f"worker pid={p.pid} did not finish in time")
            _assert(p.exitcode == 0, f"worker pid={p.pid} exitcode={p.exitcode}")

        results = [queue.get(timeout=2) for _ in range(PROCESS_COUNT)]
        results.sort(key=lambda r: r["worker_id"])

        total_success = sum(int(r["successes"]) for r in results)
        total_lock_retries = sum(int(r["lock_retries"]) for r in results)
        total_unrecovered_locks = sum(int(r["unrecovered_lock_errors"]) for r in results)
        all_hard_errors = [err for r in results for err in r["hard_errors"]]

        expected_success = PROCESS_COUNT * OPS_PER_PROCESS
        _assert(total_success == expected_success, f"success mismatch: {total_success} != {expected_success}")
        _assert(total_unrecovered_locks == 0, f"unrecovered lock errors: {total_unrecovered_locks}")
        _assert(not all_hard_errors, f"hard errors: {all_hard_errors}")

        conn = sqlite3.connect(db_path)
        try:
            row_count = int(conn.execute("SELECT COUNT(*) FROM writes_log").fetchone()[0])
            _assert(row_count == expected_success, f"writes_log rows mismatch: {row_count} != {expected_success}")

            kv_rows = conn.execute(
                "SELECT k, v FROM kv WHERE k LIKE 'worker_%_last_seq' ORDER BY k"
            ).fetchall()
            _assert(len(kv_rows) == PROCESS_COUNT, f"kv worker rows mismatch: {len(kv_rows)} != {PROCESS_COUNT}")
            for _, value in kv_rows:
                _assert(int(value) == OPS_PER_PROCESS - 1, f"unexpected last seq in kv: {value}")

            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            _assert(integrity and integrity[0] == "ok", f"integrity_check failed: {integrity}")
        finally:
            conn.close()

        print(
            "OK: sqlite concurrency smoke passed "
            f"(writers={PROCESS_COUNT}, ops={OPS_PER_PROCESS}, lock_retries={total_lock_retries})."
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    # Deterministic jitter across runs.
    random.seed(int(time.time()) ^ os.getpid())
    main()
