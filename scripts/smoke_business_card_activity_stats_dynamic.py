#!/usr/bin/env python3
"""
Dynamic smoke test: business card activity stats counters.

Validates end-to-end contract:
- click actions are persisted into `place_clicks_daily` via `record_place_click()`
- owner card (`build_business_card_text`) shows per-action counters
- total CTA clicks and CTR are calculated from those counters and views
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
import time
import types
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


def _setup_temp_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO general_services(name) VALUES(?)", ("__smoke_activity_stats__",))
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

    from business.handlers import build_business_card_text  # noqa: WPS433
    from business.repository import BusinessRepository  # noqa: WPS433
    from business.service import BusinessCabinetService  # noqa: WPS433
    from database import record_place_click, record_place_view  # noqa: WPS433

    repo = BusinessRepository()
    service = BusinessCabinetService(repository=repo)

    admin_id = 810001
    service.admin_ids.add(admin_id)

    services = await repo.list_services()
    _assert(bool(services), "no services available in temp DB")
    service_id = int(services[0].get("id") or 0)
    _assert(service_id > 0, f"invalid service id: {services[0]}")

    stamp = int(time.time())
    owner_tg_user_id = 820000 + (stamp % 10000)

    created = await service.register_new_business(
        tg_user_id=owner_tg_user_id,
        service_id=service_id,
        place_name=f"Activity Stats Smoke {stamp}",
        description="activity smoke",
        address="addr",
    )
    owner = created.get("owner") or {}
    place = created.get("place") or {}
    owner_id = int(owner.get("id") or 0)
    place_id = int(place.get("id") or 0)
    _assert(owner_id > 0 and place_id > 0, f"invalid created objects: {created}")

    await service.approve_owner_request(admin_id, owner_id)
    await service.change_subscription_tier(owner_tg_user_id, place_id, "partner")

    views_count = 40
    for _ in range(views_count):
        await record_place_view(place_id)

    action_counts: dict[str, int] = {
        "coupon_open": 3,
        "chat": 2,
        "call": 1,
        "link": 4,
        "menu": 5,
        "order": 6,
        "logo_open": 2,
        "partner_photo_1": 1,
        "partner_photo_2": 2,
        "partner_photo_3": 3,
        "offer1_image": 1,
        "offer2_image": 2,
    }

    for action, count in action_counts.items():
        for _ in range(count):
            await record_place_click(place_id, action)

    for action, expected_count in action_counts.items():
        got = await repo.get_place_clicks_sum(place_id, action=action, days=30)
        _assert(int(got) == int(expected_count), f"{action} aggregate mismatch: got={got}, want={expected_count}")

    owner_rows = await service.list_user_businesses(owner_tg_user_id)
    owner_item = next((row for row in owner_rows if int(row.get("place_id") or 0) == place_id), None)
    _assert(owner_item is not None, f"owner business row not found for place_id={place_id}")

    text = await build_business_card_text(owner_item, days=30)
    expected_lines = [
        "• Перегляди картки: <b>40</b>",
        "• Відкриття промокоду: <b>3</b>",
        "• Відкриття чату: <b>2</b>",
        "• Відкриття дзвінка: <b>1</b>",
        "• Відкриття посилання: <b>4</b>",
        "• Відкриття меню/прайсу: <b>5</b>",
        "• Відкриття замовлення/запису: <b>6</b>",
        "• Відкриття логотипу/фото: <b>2</b>",
        "• Відкриття фото 1 (Partner): <b>1</b>",
        "• Відкриття фото 2 (Partner): <b>2</b>",
        "• Відкриття фото 3 (Partner): <b>3</b>",
        "• Відкриття фото оферу 1: <b>1</b>",
        "• Відкриття фото оферу 2: <b>2</b>",
    ]
    for line in expected_lines:
        _assert(line in text, f"missing stats line in business card: {line}\n---\n{text}")

    total_clicks = sum(action_counts.values())
    ctr = round((total_clicks * 100.0) / views_count, 1)
    _assert(
        f"• Усі кліки по кнопках: <b>{total_clicks}</b>" in text,
        f"missing total-clicks line: total={total_clicks}\n---\n{text}",
    )
    _assert(
        f"• CTR кнопок: <b>{ctr}%</b>" in text,
        f"missing ctr line: ctr={ctr}\n---\n{text}",
    )


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="powerbot-smoke-activity-stats-"))
    try:
        db_path = tmpdir / "state.db"
        _setup_temp_db(db_path)

        os.environ["DB_PATH"] = str(db_path)
        os.environ.setdefault("BOT_TOKEN", "smoke-test-token")
        os.environ.setdefault("ADMIN_IDS", "810001")
        os.environ["BUSINESS_MODE"] = "1"

        import sys

        sys.path.insert(0, str(REPO_ROOT / "src"))

        asyncio.run(_run_checks())
        print("OK: business card activity stats dynamic smoke passed.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
