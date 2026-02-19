#!/usr/bin/env python3
"""
Smoke test: resident place search by keywords/name/address.

Checks:
- `search_places` finds published places by keyword token (e.g. "сирники").
- Search is case-insensitive and punctuation-tolerant.
- Unpublished places are excluded.
- Tie-break by likes works when match score is equal.
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


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _setup_temp_db(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("Кав'ярні",))
        service_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        conn.execute(
            """
            INSERT INTO places(service_id, name, description, address, keywords, is_published)
            VALUES(?, ?, ?, ?, ?, 1)
            """,
            (
                service_id,
                "TC&F",
                "Домашні десерти",
                "Ньюкасл (24-в)",
                "сирники десерти випічка",
            ),
        )
        place_keyword = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        conn.execute(
            """
            INSERT INTO places(service_id, name, description, address, keywords, is_published)
            VALUES(?, ?, ?, ?, ?, 1)
            """,
            (
                service_id,
                "Сирники House",
                "Сніданки та кава",
                "Брістоль (24-б)",
                "сніданки кава",
            ),
        )
        place_name = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        conn.execute(
            """
            INSERT INTO places(service_id, name, description, address, keywords, is_published)
            VALUES(?, ?, ?, ?, ?, 0)
            """,
            (
                service_id,
                "Secret Syrnyk Lab",
                "Тестовий unpublished заклад",
                "Лондон (28-е)",
                "сирники тест",
            ),
        )
        place_hidden = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        # Equal match score for place_keyword/place_name; likes must break tie.
        for chat_id in (101, 102, 103):
            conn.execute(
                "INSERT INTO place_likes(place_id, chat_id, liked_at) VALUES(?, ?, ?)",
                (place_keyword, chat_id, _iso_now()),
            )
        conn.execute(
            "INSERT INTO place_likes(place_id, chat_id, liked_at) VALUES(?, ?, ?)",
            (place_name, 201, _iso_now()),
        )
        conn.commit()
        return {
            "service_id": service_id,
            "place_keyword": place_keyword,
            "place_name": place_name,
            "place_hidden": place_hidden,
        }
    finally:
        conn.close()


async def _run_checks(ids: dict[str, int]) -> None:
    import sys

    if "dotenv" not in sys.modules:
        dotenv_stub = types.ModuleType("dotenv")

        def _noop_load_dotenv(*_args, **_kwargs) -> bool:
            return False

        dotenv_stub.load_dotenv = _noop_load_dotenv  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv_stub

    from database import search_places  # noqa: WPS433

    by_keyword = await search_places("сирники")
    keyword_ids = [int(row["id"]) for row in by_keyword]
    _assert(ids["place_keyword"] in keyword_ids, f"keyword place not found: {keyword_ids}")
    _assert(ids["place_name"] in keyword_ids, f"name place not found: {keyword_ids}")
    _assert(ids["place_hidden"] not in keyword_ids, f"unpublished place leaked into search: {keyword_ids}")
    _assert(
        keyword_ids.index(ids["place_keyword"]) < keyword_ids.index(ids["place_name"]),
        f"likes tie-break order mismatch: {keyword_ids}",
    )

    by_upper = await search_places("СИРНИКИ")
    upper_ids = [int(row["id"]) for row in by_upper]
    _assert(ids["place_keyword"] in upper_ids and ids["place_name"] in upper_ids, f"case-insensitive match failed: {upper_ids}")

    by_phrase = await search_places("де сирники?")
    phrase_ids = [int(row["id"]) for row in by_phrase]
    _assert(ids["place_keyword"] in phrase_ids, f"tokenized phrase search failed: {phrase_ids}")

    no_results = await search_places("манго шейк premium")
    _assert(no_results == [], f"unexpected results for unknown query: {no_results}")


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-search-"))
    try:
        db_path = tmpdir / "state.db"
        ids = _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "1")

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))
        asyncio.run(_run_checks(ids))
        print("OK: search places keywords smoke test passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
