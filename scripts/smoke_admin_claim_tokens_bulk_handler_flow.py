#!/usr/bin/env python3
"""
Dynamic smoke test: admin claim-tokens bulk-generation handler flow.

Validates callback chain:
- `cb_biz_tok_gen_all` renders confirm screen
- `cb_biz_tok_gen_all_confirm` performs bulk rotation and renders success note
- DB has exactly one active token per place after flow
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
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_bulk_claim_handler__",))
        service_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        places_total = 4
        for idx in range(1, places_total + 1):
            conn.execute(
                """
                INSERT INTO places(service_id, name, description, address, is_published, business_enabled)
                VALUES(?, ?, 'desc', 'addr', 1, 1)
                """,
                (service_id, f"Bulk Claim Handler Place {idx}"),
            )
        conn.commit()
        return service_id, places_total
    finally:
        conn.close()


def _extract_counts(text: str) -> tuple[int, int]:
    match = re.search(r"Згенеровано:\s*<b>(\d+)</b>\s*з\s*<b>(\d+)</b>", text)
    if not match:
        raise AssertionError(f"cannot parse rotated/total counts from text: {text}")
    return int(match.group(1)), int(match.group(2))


async def _run_checks(db_path: Path, expected_places_total: int, admin_id: int) -> None:
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
                chat=SimpleNamespace(id=801001),
                message_id=88,
                bot=SimpleNamespace(),
            )

        async def answer(self, *args, **kwargs):
            answer_calls.append((args, kwargs))

    ah.render = _fake_render
    try:
        await ah.cb_biz_tok_gen_all(_DummyCallback(ah.CB_BIZ_TOK_GEN_ALL))  # type: ignore[arg-type]
        _assert(render_calls, "bulk-confirm step did not render")
        confirm_screen = render_calls[-1]
        _assert("Увага" in confirm_screen["text"], f"unexpected confirm text: {confirm_screen}")
        _assert("всіх" in confirm_screen["text"], f"confirm text must mention all places: {confirm_screen}")

        await ah.cb_biz_tok_gen_all_confirm(_DummyCallback(ah.CB_BIZ_TOK_GEN_ALL_CONFIRM))  # type: ignore[arg-type]
        _assert(len(render_calls) >= 2, "bulk-confirm handler did not render result screen")
        result_screen = render_calls[-1]
        _assert("Коди прив'язки" in result_screen["text"], f"unexpected result screen: {result_screen}")
        _assert("Готово" in result_screen["text"], f"success note missing: {result_screen}")
        rotated, total = _extract_counts(result_screen["text"])
        _assert(total == expected_places_total, f"total places mismatch: expected={expected_places_total}, got={total}")
        _assert(rotated == expected_places_total, f"rotated count mismatch: expected={expected_places_total}, got={rotated}")
    finally:
        ah.render = original_render

    # Verify no alert-level errors were emitted.
    alert_errors = []
    for args, kwargs in answer_calls:
        text = str(args[0]) if args else ""
        if bool(kwargs.get("show_alert")) and text.startswith("❌"):
            alert_errors.append(text)
    _assert(not alert_errors, f"unexpected alert errors: {alert_errors}")

    # DB-level checks: one active token per place, no duplicate active tokens.
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT place_id, token
              FROM business_claim_tokens
             WHERE status = 'active'
             ORDER BY place_id
            """
        ).fetchall()
        _assert(len(rows) == expected_places_total, f"active token rows mismatch: {rows}")
        unique_places = {int(row[0]) for row in rows}
        unique_tokens = {str(row[1]) for row in rows}
        _assert(len(unique_places) == expected_places_total, f"duplicate place_id in active tokens: {rows}")
        _assert(len(unique_tokens) == expected_places_total, f"duplicate token in active tokens: {rows}")
    finally:
        conn.close()


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-admin-claim-bulk-"))
    admin_id = 880002
    try:
        db_path = tmpdir / "state.db"
        _service_id, places_total = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ["ADMIN_IDS"] = str(admin_id)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_BOT_API_KEY", "smoke-admin-token")
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks(db_path, places_total, admin_id))
        print("OK: admin claim-tokens bulk handler flow smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()

