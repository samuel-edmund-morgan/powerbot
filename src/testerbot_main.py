#!/usr/bin/env python3
"""Standalone Telegram userbot runtime for E2E test smoke scenarios."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass

from logging_setup import configure_logging
from testerbot.client import TesterbotConfig, build_telethon_client, ensure_enabled
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


@dataclass
class TesterContext:
    client: any
    cfg: TesterbotConfig
    timeout_sec: int


    async def wait_msg(self, conv):
        # Wait for edited or new message response.
        return await _wait_for_bot_update(conv, self.timeout_sec)


async def _wait_for_bot_update(conv, timeout_sec: int):
    import asyncio as _asyncio
    edit_task = _asyncio.create_task(conv.get_edit(timeout=timeout_sec))
    resp_task = _asyncio.create_task(conv.get_response(timeout=timeout_sec))
    done, pending = await _asyncio.wait(
        {edit_task, resp_task},
        return_when=_asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    for task in done:
        return task.result()
    raise RuntimeError("No bot update received")


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
            results.append(
                ScenarioReport(
                    name=name,
                    status="error",
                    duration_ms=0,
                    message=str(exc),
                )
            )
            break
    return results


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
            scenario_results = await _run_scenarios(ctx)
            report = mark_finished(report, scenario_results)
            write_report(report, cfg.report_path)
            junit_path = os.getenv("TESTERBOT_JUNIT_PATH", "").strip()
            if junit_path:
                write_junit_xml(report, junit_path)

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
