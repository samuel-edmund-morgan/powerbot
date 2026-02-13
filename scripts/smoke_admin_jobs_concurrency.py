#!/usr/bin/env python3
"""
SQLite concurrency smoke-check for admin_jobs queue.

What it validates:
- multiple workers can claim/process admin_jobs without duplicate claims
- retry/backoff handles transient `database is locked`
- queue reaches consistent terminal state (`done`) with no lost jobs

Run:
  python3 scripts/smoke_admin_jobs_concurrency.py
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


WORKERS = 4
JOBS_TOTAL = 180
BUSY_TIMEOUT_MS = 5000
RETRY_ATTEMPTS = 8
RETRY_BASE_DELAY_SEC = 0.002


def _init_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS};")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                created_by INTEGER,
                started_at TEXT,
                finished_at TEXT,
                progress_current INTEGER NOT NULL DEFAULT 0,
                progress_total INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_admin_jobs_status_created ON admin_jobs (status, created_at)"
        )
        conn.executemany(
            """
            INSERT INTO admin_jobs(kind, payload_json, status, created_at, created_by)
            VALUES('smoke', '{}', 'pending', strftime('%Y-%m-%dT%H:%M:%f', 'now'), 1)
            """,
            [tuple() for _ in range(JOBS_TOTAL)],
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


def _claim_next_pending(conn: sqlite3.Connection) -> int | None:
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        """
        SELECT id
        FROM admin_jobs
        WHERE status = 'pending'
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        conn.execute("COMMIT")
        return None
    job_id = int(row[0])
    conn.execute(
        """
        UPDATE admin_jobs
        SET status = 'running',
            started_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'),
            progress_current = 0,
            progress_total = 1
        WHERE id = ? AND status = 'pending'
        """,
        (job_id,),
    )
    if int(conn.total_changes) <= 0:
        # Lost race to another worker; keep queue progress monotonic and retry.
        conn.execute("COMMIT")
        return -1
    conn.execute("COMMIT")
    return job_id


def _finish_job_done(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        UPDATE admin_jobs
        SET status = 'done',
            finished_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'),
            progress_current = 1,
            progress_total = 1,
            error = NULL
        WHERE id = ? AND status = 'running'
        """,
        (int(job_id),),
    )
    conn.execute("COMMIT")


def _with_retry(fn, conn: sqlite3.Connection):
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return fn(conn)
        except sqlite3.OperationalError as error:
            _rollback_if_needed(conn)
            if "database is locked" not in str(error).lower():
                raise
            if attempt >= RETRY_ATTEMPTS - 1:
                raise
            delay = RETRY_BASE_DELAY_SEC * (2**attempt) + random.uniform(0, RETRY_BASE_DELAY_SEC)
            time.sleep(delay)


def _worker(db_path: str, worker_id: int, q: mp.Queue) -> None:
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS};")

    done = 0
    claim_conflicts = 0
    lock_errors = 0
    errors: list[str] = []

    try:
        while True:
            try:
                claimed = _with_retry(_claim_next_pending, conn)
            except sqlite3.OperationalError as error:
                lock_errors += 1
                errors.append(f"claim OperationalError: {error}")
                break
            except Exception as error:  # noqa: BLE001
                errors.append(f"claim {type(error).__name__}: {error}")
                break

            if claimed is None:
                break
            if claimed == -1:
                claim_conflicts += 1
                time.sleep(random.uniform(0, 0.002))
                continue

            # Simulate tiny non-DB work between short transactions.
            time.sleep(random.uniform(0, 0.003))

            try:
                _with_retry(lambda db: _finish_job_done(db, int(claimed)), conn)
                done += 1
            except sqlite3.OperationalError as error:
                lock_errors += 1
                errors.append(f"finish OperationalError: {error}")
                break
            except Exception as error:  # noqa: BLE001
                errors.append(f"finish {type(error).__name__}: {error}")
                break
    finally:
        try:
            conn.close()
        except Exception:
            pass

    q.put(
        {
            "worker_id": int(worker_id),
            "done": int(done),
            "claim_conflicts": int(claim_conflicts),
            "lock_errors": int(lock_errors),
            "errors": errors,
        }
    )


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-admin-jobs-"))
    try:
        db_path = tmpdir / "state.db"
        _init_db(db_path)

        ctx = mp.get_context("spawn")
        queue: mp.Queue = ctx.Queue()
        procs: list[mp.Process] = []
        for worker_id in range(1, WORKERS + 1):
            p = ctx.Process(target=_worker, args=(str(db_path), worker_id, queue), daemon=False)
            p.start()
            procs.append(p)

        for p in procs:
            p.join(timeout=120)
        for p in procs:
            _assert(not p.is_alive(), f"worker pid={p.pid} did not finish in time")
            _assert(p.exitcode == 0, f"worker pid={p.pid} exitcode={p.exitcode}")

        results = [queue.get(timeout=2) for _ in range(WORKERS)]
        results.sort(key=lambda item: item["worker_id"])
        total_done = sum(int(item["done"]) for item in results)
        total_conflicts = sum(int(item["claim_conflicts"]) for item in results)
        total_lock_errors = sum(int(item["lock_errors"]) for item in results)
        all_errors = [error for item in results for error in item["errors"]]

        _assert(total_done == JOBS_TOTAL, f"processed jobs mismatch: {total_done} != {JOBS_TOTAL}")
        _assert(total_lock_errors == 0, f"unexpected lock errors: {total_lock_errors}")
        _assert(not all_errors, f"worker errors: {all_errors}")

        conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000)
        try:
            pending = int(conn.execute("SELECT COUNT(*) FROM admin_jobs WHERE status='pending'").fetchone()[0])
            running = int(conn.execute("SELECT COUNT(*) FROM admin_jobs WHERE status='running'").fetchone()[0])
            done = int(conn.execute("SELECT COUNT(*) FROM admin_jobs WHERE status='done'").fetchone()[0])
            duplicate_done = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM (
                        SELECT id
                        FROM admin_jobs
                        WHERE status='done'
                        GROUP BY id
                        HAVING COUNT(*) > 1
                    )
                    """
                ).fetchone()[0]
            )
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()

        _assert(pending == 0, f"pending jobs remain: {pending}")
        _assert(running == 0, f"running jobs remain: {running}")
        _assert(done == JOBS_TOTAL, f"done jobs mismatch: {done} != {JOBS_TOTAL}")
        _assert(duplicate_done == 0, f"duplicate done rows detected: {duplicate_done}")
        _assert(integrity and integrity[0] == "ok", f"integrity_check failed: {integrity}")

        print(
            "OK: admin_jobs concurrency smoke passed "
            f"(workers={WORKERS}, jobs={JOBS_TOTAL}, claim_conflicts={total_conflicts})."
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    random.seed(int(time.time()) ^ os.getpid())
    main()
