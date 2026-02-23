#!/usr/bin/env python3
"""Standalone Telegram userbot runtime for E2E test smoke scenarios."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from logging_setup import configure_logging
from testerbot.client import TesterbotConfig, build_telethon_client, ensure_enabled
from testerbot.callbacks import parse_callback_inventory
from testerbot.reporting import (
    ScenarioReport,
    TesterbotRunReport,
    build_empty_report,
    first_error_message,
    format_text_summary,
    mark_finished,
    write_junit_xml,
    write_report,
)
from testerbot.scenarios import resident as resident_scenario
from testerbot.scenarios import admin as admin_scenario
from testerbot.scenarios import business as business_scenario

configure_logging("testerbot")
logger = logging.getLogger(__name__)
_SAFE_SQLITE_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class TesterContext:
    client: any
    cfg: TesterbotConfig
    timeout_sec: int
    seen_callbacks: dict[str, set[str]] = field(default_factory=dict)
    clicked_callbacks: dict[str, set[str]] = field(default_factory=dict)


    async def wait_msg(self, conv):
        # Wait for edited or new message response.
        return await _wait_for_bot_update(conv, self.timeout_sec)

    def record_seen_callbacks(self, bot_name: str, callbacks: set[str]) -> None:
        if not callbacks:
            return
        bucket = self.seen_callbacks.setdefault(bot_name, set())
        bucket.update(callbacks)

    def record_clicked_callback(self, bot_name: str, callback_data: str | None) -> None:
        value = str(callback_data or "").strip()
        if not value:
            return
        bucket = self.clicked_callbacks.setdefault(bot_name, set())
        bucket.add(value)


async def _wait_for_bot_update(conv, timeout_sec: int):
    import asyncio as _asyncio

    loop = _asyncio.get_running_loop()
    deadline = loop.time() + max(timeout_sec, 1)
    last_error: Exception | None = None
    while loop.time() < deadline:
        remaining = max(0.1, deadline - loop.time())
        window = min(2.0, remaining)
        try:
            return await conv.get_edit(timeout=window)
        except Exception as exc:  # pragma: no cover - runtime dependent
            last_error = exc
        remaining = max(0.1, deadline - loop.time())
        window = min(2.0, remaining)
        try:
            return await conv.get_response(timeout=window)
        except Exception as exc:  # pragma: no cover - runtime dependent
            last_error = exc
    if last_error is not None:
        raise TimeoutError("No bot update received within timeout") from last_error
    raise TimeoutError("No bot update received within timeout")


async def _run_scenarios(ctx: TesterContext) -> list[ScenarioReport]:
    scenarios = [
        resident_scenario,
        admin_scenario,
        business_scenario,
    ]

    results: list[ScenarioReport] = []
    for scenario in scenarios:
        name = scenario.__name__.replace("_", " ").strip()
        try:
            result = await scenario.run(ctx)
            scenario_name = result.name  # type: ignore[attr-defined]
            status = result.status  # type: ignore[attr-defined]
            duration_ms = result.duration_ms  # type: ignore[attr-defined]
            message = result.message  # type: ignore[attr-defined]

            results.append(
                ScenarioReport(
                    name=scenario_name,
                    status=status,
                    duration_ms=int(duration_ms),
                    message=message,
                )
            )
            logger.info("scenario %s: %s", scenario_name, status)
            if status != "ok":
                break
        except Exception as exc:  # pragma: no cover
            logger.exception("scenario %s failed", name)
            reason = f"{name}: {exc.__class__.__name__}: {exc}"
            results.append(
                ScenarioReport(
                    name=name,
                    status="error",
                    duration_ms=0,
                    message=reason,
                )
            )
            break
    return results


def _snapshot_table_counts(db_path: str, tables: tuple[str, ...]) -> dict[str, int]:
    db = Path(db_path)
    if not db.exists():
        logger.warning("testerbot idempotence guard: db path does not exist: %s", db_path)
        return {}
    if not tables:
        return {}

    counts: dict[str, int] = {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    try:
        for table in tables:
            table_name = table.strip()
            if not table_name:
                continue
            if not _SAFE_SQLITE_TABLE_NAME.match(table_name):
                logger.warning("testerbot idempotence guard: skip unsafe table name `%s`", table_name)
                continue
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
            except sqlite3.Error as exc:
                logger.warning(
                    "testerbot idempotence guard: skip table `%s`: %s",
                    table_name,
                    exc,
                )
                continue
            counts[table_name] = int(row[0] if row else 0)
    finally:
        conn.close()
    return counts


def _idempotence_error(before: dict[str, int], after: dict[str, int]) -> str | None:
    if not before and not after:
        return None
    changed: list[str] = []
    for name in sorted(set(before) | set(after)):
        b = before.get(name)
        a = after.get(name)
        if b != a:
            changed.append(f"{name}:{b}->{a}")
    if not changed:
        return None
    return (
        "testerbot idempotence guard failed: protected table row counts changed: "
        + ", ".join(changed)
    )


def _coverage_missing(
    seen: set[str],
    clicked: set[str],
    inventory: dict[str, set[str]],
) -> dict[str, set[str]]:
    missing_eq: set[str] = set()
    for value in inventory.get("eq", set()):
        if value not in seen and value not in clicked:
            missing_eq.add(value)

    missing_sw: set[str] = set()
    for prefix in inventory.get("startswith", set()):
        if not any(x.startswith(prefix) for x in seen) and not any(x.startswith(prefix) for x in clicked):
            missing_sw.add(prefix)

    missing_rgx: set[str] = set()
    for pattern in inventory.get("regexp", set()):
        try:
            rx = re.compile(pattern)
        except re.error:
            missing_rgx.add(pattern)
            continue
        if not any(rx.search(x) for x in seen) and not any(rx.search(x) for x in clicked):
            missing_rgx.add(pattern)

    return {"eq": missing_eq, "startswith": missing_sw, "regexp": missing_rgx}


async def main() -> int:
    if not ensure_enabled():
        logger.info("TESTERBOT_ENABLED=0, skipping testerbot run.")
        return 0

    cfg = TesterbotConfig.from_env()
    report = build_empty_report()
    client = build_telethon_client(cfg)

    await client.connect()
    if not await client.is_user_authorized():
        logger.error("Telethon session is not authorized.")
        await client.disconnect()
        return 2

    try:
        async with client:
            if cfg.allowed_chat_ids:
                try:
                    allowed = set(cfg.allowed_chat_ids)
                    bot_entity_ids = (
                        await client.get_peer_id(cfg.targets.powerbot),
                        await client.get_peer_id(cfg.targets.adminbot),
                        await client.get_peer_id(cfg.targets.businessbot),
                    )
                    for bot_id in bot_entity_ids:
                        if bot_id not in allowed:
                            logger.error(
                                "Allowed chat IDs guard failed. bot_id=%s not in TESTERBOT_ALLOWED_CHAT_IDS", bot_id
                            )
                            return 2
                except Exception as exc:
                    logger.error("Failed to validate testerbot allowlist: %s", exc)
                    return 2

            ctx = TesterContext(client=client, cfg=cfg, timeout_sec=cfg.timeout_sec)
            baseline_counts = _snapshot_table_counts(cfg.db_path, cfg.idempotence_tables)
            scenario_results = await _run_scenarios(ctx)
            if baseline_counts:
                final_counts = _snapshot_table_counts(cfg.db_path, cfg.idempotence_tables)
                idempotence_error = _idempotence_error(baseline_counts, final_counts)
                if idempotence_error:
                    logger.error(idempotence_error)
                    scenario_results.append(
                        ScenarioReport(
                            name="idempotence_guard",
                            status="error",
                            duration_ms=0,
                            message=idempotence_error,
                        )
                    )

            report = mark_finished(report, scenario_results)
            write_report(report, cfg.report_path)
            junit_path = os.getenv("TESTERBOT_JUNIT_PATH", "").strip()
            if junit_path:
                write_junit_xml(report, junit_path)

            # Callback coverage telemetry (for full-click roadmap).
            try:
                strict = str(os.getenv("TESTERBOT_CALLBACK_COVERAGE_STRICT", "0")).strip() == "1"
                repo_root = Path(__file__).resolve().parents[1]
                inventory = parse_callback_inventory(repo_root)
                coverage_lines: list[str] = []
                coverage_failed = False
                for bot_name in ("resident", "admin", "business"):
                    inv = inventory.get(bot_name, {"eq": set(), "startswith": set(), "regexp": set()})
                    seen = ctx.seen_callbacks.get(bot_name, set())
                    clicked = ctx.clicked_callbacks.get(bot_name, set())
                    missing = _coverage_missing(seen, clicked, inv)
                    total_rules = (
                        len(inv.get("eq", set()))
                        + len(inv.get("startswith", set()))
                        + len(inv.get("regexp", set()))
                    )
                    missing_total = (
                        len(missing.get("eq", set()))
                        + len(missing.get("startswith", set()))
                        + len(missing.get("regexp", set()))
                    )
                    coverage_lines.append(
                        f"{bot_name}: seen={len(seen)} clicked={len(clicked)} inventory={total_rules} missing={missing_total}"
                    )
                    if strict and missing_total > 0:
                        coverage_failed = True
                        logger.error(
                            "callback coverage strict fail for %s: missing eq=%s startswith=%s regexp=%s",
                            bot_name,
                            sorted(missing.get("eq", set())),
                            sorted(missing.get("startswith", set())),
                            sorted(missing.get("regexp", set())),
                        )
                logger.info("testerbot callback coverage: %s", " | ".join(coverage_lines))
                if strict and coverage_failed:
                    scenario_results.append(
                        ScenarioReport(
                            name="callback_coverage_strict",
                            status="error",
                            duration_ms=0,
                            message="missing callback coverage in strict mode",
                        )
                    )
                    report = mark_finished(report, scenario_results)
                    write_report(report, cfg.report_path)
                    if junit_path:
                        write_junit_xml(report, junit_path)
            except Exception:
                logger.exception("testerbot callback coverage telemetry failed")

            summary = format_text_summary(report)
            logger.info(summary)
            if report.failed > 0:
                logger.error("first_error=%s", first_error_message(report) or "<unknown>")
            else:
                logger.info("all scenarios passed")

            if report.failed > 0:
                for scenario in scenario_results:
                    if scenario.status != "ok":
                        logger.error("failed scenario=%s reason=%s", scenario.name, scenario.message)
                return 1

            return 0
    finally:
        if client.is_connected():
            await client.disconnect()


if __name__ == "__main__":
    code = asyncio.run(main())
    if code != 0:
        sys.exit(code)
