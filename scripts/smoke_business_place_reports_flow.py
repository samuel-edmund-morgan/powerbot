#!/usr/bin/env python3
"""
Dynamic smoke test: place-reports data flow and moderation ordering.

Checks:
- report creation works only for published places
- pending list is ordered by priority_score DESC first, then recency
- resolving a report moves it out of pending and stores resolver metadata
- admin alert job payload for place-report can be enqueued/retrieved
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
import types
from datetime import datetime, timedelta, timezone
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
    raise FileNotFoundError("Cannot locate repo root with schema.sql and src/")


REPO_ROOT = _resolve_repo_root()
SCHEMA_SQL = REPO_ROOT / "schema.sql"


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _setup_temp_db(db_path: Path) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("Кав'ярні",))
        service_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        # Published places used for report priority ordering.
        conn.execute(
            """
            INSERT INTO places(service_id, name, description, address, is_published, business_enabled)
            VALUES(?, ?, ?, ?, 1, 1)
            """,
            (service_id, "Priority PRO", "desc", "addr"),
        )
        place_pro = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        conn.execute(
            """
            INSERT INTO places(service_id, name, description, address, is_published, business_enabled)
            VALUES(?, ?, ?, ?, 1, 1)
            """,
            (service_id, "Priority LIGHT", "desc", "addr"),
        )
        place_light = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        conn.execute(
            """
            INSERT INTO places(service_id, name, description, address, is_published, business_enabled)
            VALUES(?, ?, ?, ?, 1, 0)
            """,
            (service_id, "Priority FREE", "desc", "addr"),
        )
        place_free = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        # Hidden place: report creation must fail.
        conn.execute(
            """
            INSERT INTO places(service_id, name, description, address, is_published, business_enabled)
            VALUES(?, ?, ?, ?, 0, 1)
            """,
            (service_id, "Hidden Place", "desc", "addr"),
        )
        place_hidden = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        # Active paid subscriptions define moderation priority.
        pro_expires = _iso(now + timedelta(days=20))
        light_expires = _iso(now + timedelta(days=20))
        starts = _iso(now - timedelta(days=1))
        created = _iso(now - timedelta(days=2))

        conn.execute(
            """
            INSERT INTO business_subscriptions(place_id, tier, status, starts_at, expires_at, created_at, updated_at)
            VALUES(?, 'pro', 'active', ?, ?, ?, ?)
            """,
            (place_pro, starts, pro_expires, created, created),
        )
        conn.execute(
            """
            INSERT INTO business_subscriptions(place_id, tier, status, starts_at, expires_at, created_at, updated_at)
            VALUES(?, 'light', 'active', ?, ?, ?, ?)
            """,
            (place_light, starts, light_expires, created, created),
        )
        conn.execute(
            """
            INSERT INTO business_subscriptions(place_id, tier, status, created_at, updated_at)
            VALUES(?, 'free', 'inactive', ?, ?)
            """,
            (place_free, created, created),
        )

        conn.commit()
        return {
            "service_id": service_id,
            "place_pro": place_pro,
            "place_light": place_light,
            "place_free": place_free,
            "place_hidden": place_hidden,
        }
    finally:
        conn.close()


def _set_report_created_at(db_path: Path, report_id: int, created_at: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE place_reports SET created_at=? WHERE id=?",
            (str(created_at), int(report_id)),
        )
        conn.commit()
    finally:
        conn.close()


async def _run_checks(db_path: Path, ids: dict[str, int]) -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    from database import (  # noqa: WPS433
        create_admin_job,
        create_place_report,
        get_admin_job,
        init_db,
        list_place_reports,
        set_place_report_status,
    )

    now = datetime.now(timezone.utc)
    await init_db()

    report_pro = await create_place_report(
        place_id=int(ids["place_pro"]),
        reporter_tg_user_id=7001,
        reporter_username="pro_owner",
        reporter_first_name="Pro",
        reporter_last_name="Owner",
        report_text="Перевірте опис закладу PRO",
    )
    _assert(report_pro is not None, "expected report for published PRO place")

    report_light = await create_place_report(
        place_id=int(ids["place_light"]),
        reporter_tg_user_id=7002,
        reporter_username="light_owner",
        reporter_first_name="Light",
        reporter_last_name=None,
        report_text="Перевірте контакти закладу LIGHT",
    )
    _assert(report_light is not None, "expected report for published LIGHT place")

    report_free = await create_place_report(
        place_id=int(ids["place_free"]),
        reporter_tg_user_id=7003,
        reporter_username="free_owner",
        reporter_first_name="Free",
        reporter_last_name=None,
        report_text="Перевірте адресу закладу FREE",
    )
    _assert(report_free is not None, "expected report for published FREE place")

    # Not published -> must not create report.
    hidden_report = await create_place_report(
        place_id=int(ids["place_hidden"]),
        reporter_tg_user_id=7004,
        reporter_username="hidden_owner",
        reporter_first_name="Hidden",
        reporter_last_name=None,
        report_text="Це не має створитись",
    )
    _assert(hidden_report is None, "report must not be created for unpublished place")

    empty_text_report = await create_place_report(
        place_id=int(ids["place_free"]),
        reporter_tg_user_id=7005,
        reporter_username="empty_owner",
        reporter_first_name="Empty",
        reporter_last_name=None,
        report_text="   ",
    )
    _assert(empty_text_report is None, "report with empty text must be ignored")

    # Force timestamps to validate ordering: priority must win over recency.
    _set_report_created_at(db_path, int(report_pro["id"]), _iso(now - timedelta(minutes=10)))
    _set_report_created_at(db_path, int(report_light["id"]), _iso(now - timedelta(minutes=5)))
    _set_report_created_at(db_path, int(report_free["id"]), _iso(now - timedelta(minutes=1)))

    pending_rows, pending_total = await list_place_reports(status="pending", limit=20, offset=0)
    _assert(pending_total == 3, f"pending total mismatch: {pending_total}")
    ordered_ids = [int(row["id"]) for row in pending_rows]
    expected_order = [int(report_pro["id"]), int(report_light["id"]), int(report_free["id"])]
    _assert(
        ordered_ids == expected_order,
        f"priority ordering mismatch: expected={expected_order} got={ordered_ids}",
    )
    _assert(int(pending_rows[0]["priority_score"]) == 2, f"pro priority mismatch: {pending_rows[0]}")
    _assert(int(pending_rows[1]["priority_score"]) == 1, f"light priority mismatch: {pending_rows[1]}")
    _assert(int(pending_rows[2]["priority_score"]) == 0, f"free priority mismatch: {pending_rows[2]}")

    # Resolve LIGHT report and ensure it is moved out of pending.
    resolved_ok = await set_place_report_status(
        int(report_light["id"]),
        "resolved",
        resolved_by=999001,
    )
    _assert(resolved_ok, "set_place_report_status must return True on first resolve")

    pending_after, pending_after_total = await list_place_reports(status="pending", limit=20, offset=0)
    _assert(pending_after_total == 2, f"pending after resolve mismatch: {pending_after_total}")
    _assert(
        int(report_light["id"]) not in {int(row["id"]) for row in pending_after},
        "resolved report leaked into pending list",
    )

    resolved_rows, resolved_total = await list_place_reports(status="resolved", limit=20, offset=0)
    _assert(resolved_total >= 1, "resolved list must contain at least one row")
    resolved_row = next((row for row in resolved_rows if int(row["id"]) == int(report_light["id"])), None)
    _assert(resolved_row is not None, "resolved report not found by id")
    _assert(int(resolved_row["resolved_by"] or 0) == 999001, f"resolved_by mismatch: {resolved_row}")
    _assert(str(resolved_row["reporter_username"] or "") == "light_owner", "reporter metadata mismatch")

    # Job payload for report alert must be storable/retrievable.
    job_id = await create_admin_job(
        "admin_place_report_alert",
        {
            "report_id": int(report_pro["id"]),
            "place_id": int(ids["place_pro"]),
            "reporter_tg_user_id": 7001,
            "report_text": str(report_pro["report_text"]),
        },
        created_by=999001,
    )
    _assert(job_id > 0, f"invalid job id: {job_id}")
    job = await get_admin_job(int(job_id))
    _assert(job is not None, "created admin job not found")
    _assert(str(job.get("kind") or "") == "admin_place_report_alert", f"job kind mismatch: {job}")
    payload = job.get("payload") or {}
    _assert(int(payload.get("report_id") or 0) == int(report_pro["id"]), f"job payload mismatch: {payload}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-place-reports-flow-"))
    try:
        db_path = tmpdir / "state.db"
        ids = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "1")

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))
        asyncio.run(_run_checks(db_path, ids))
        print("OK: business place reports flow smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
