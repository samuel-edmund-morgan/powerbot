#!/usr/bin/env python3
"""Standalone Telegram userbot runtime for E2E test smoke scenarios."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from logging_setup import configure_logging
from testerbot.client import TesterbotConfig, build_telethon_client, ensure_enabled
from testerbot.callbacks import filter_read_only_inventory, parse_callback_inventory
from testerbot.input_contract import REQUIRED_INPUT_FLOWS
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
    active_scenario_labels: dict[str, str] = field(default_factory=dict)
    seen_callback_scenarios: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    clicked_callback_scenarios: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    input_flows: dict[str, set[str]] = field(default_factory=dict)


    async def wait_msg(self, conv):
        # Wait for edited or new message response.
        return await _wait_for_bot_update(conv, self.timeout_sec)

    def set_active_scenario(self, bot_name: str, scenario_label: str) -> None:
        value = str(scenario_label or "").strip()
        if not value:
            return
        self.active_scenario_labels[bot_name] = value

    def clear_active_scenario(self, bot_name: str) -> None:
        self.active_scenario_labels.pop(bot_name, None)

    def _record_callback_scenario(
        self,
        mapping: dict[str, dict[str, set[str]]],
        bot_name: str,
        callback_data: str,
    ) -> None:
        scenario_label = self.active_scenario_labels.get(bot_name)
        if not scenario_label:
            return
        bot_map = mapping.setdefault(bot_name, {})
        bot_map.setdefault(callback_data, set()).add(scenario_label)

    def record_seen_callbacks(self, bot_name: str, callbacks: set[str]) -> None:
        if not callbacks:
            return
        bucket = self.seen_callbacks.setdefault(bot_name, set())
        for value in callbacks:
            callback_data = str(value or "").strip()
            if not callback_data:
                continue
            bucket.add(callback_data)
            self._record_callback_scenario(
                self.seen_callback_scenarios,
                bot_name,
                callback_data,
            )

    def record_clicked_callback(self, bot_name: str, callback_data: str | None) -> None:
        value = str(callback_data or "").strip()
        if not value:
            return
        bucket = self.clicked_callbacks.setdefault(bot_name, set())
        bucket.add(value)
        self._record_callback_scenario(
            self.clicked_callback_scenarios,
            bot_name,
            value,
        )

    def record_input_flow(self, bot_name: str, flow_key: str | None) -> None:
        value = str(flow_key or "").strip()
        if not value:
            return
        bucket = self.input_flows.setdefault(bot_name, set())
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


async def _run_scenarios(ctx: TesterContext) -> tuple[list[ScenarioReport], dict[str, str]]:
    scenarios = [
        ("resident", resident_scenario),
        ("admin", admin_scenario),
        ("business", business_scenario),
    ]

    results: list[ScenarioReport] = []
    scenario_names_by_bot: dict[str, str] = {}
    for bot_name, scenario in scenarios:
        name = scenario.__name__.replace("_", " ").strip()
        ctx.set_active_scenario(bot_name, name)
        try:
            result = await scenario.run(ctx)
            scenario_name = result.name  # type: ignore[attr-defined]
            status = result.status  # type: ignore[attr-defined]
            duration_ms = result.duration_ms  # type: ignore[attr-defined]
            message = result.message  # type: ignore[attr-defined]
            scenario_names_by_bot[bot_name] = str(scenario_name)
            ctx.set_active_scenario(bot_name, str(scenario_name))

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
            scenario_names_by_bot.setdefault(bot_name, name)
            results.append(
                ScenarioReport(
                    name=name,
                    status="error",
                    duration_ms=0,
                    message=reason,
                )
            )
            break
        finally:
            ctx.clear_active_scenario(bot_name)
    return results, scenario_names_by_bot


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


def _write_callback_coverage_report(path_value: str, payload: dict) -> None:
    path = Path(path_value).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_text_report(path_value: str, text: str) -> None:
    path = Path(path_value).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_gap_report_md(payload: dict) -> str:
    bots = payload.get("bots") or {}
    lines: list[str] = [
        "# Testerbot Gap Report",
        "",
        f"- generated_at: `{payload.get('generated_at', '')}`",
        f"- strict_mode: `{payload.get('strict_mode', False)}`",
        f"- callback_inventory_total: `{payload.get('callback_inventory_total', 0)}`",
        f"- callback_uncovered_total: `{payload.get('callback_uncovered_total', 0)}`",
        f"- input_flows_total: `{payload.get('input_flows_total', 0)}`",
        f"- input_flows_missing_total: `{payload.get('input_flows_missing_total', 0)}`",
        "",
    ]

    for bot_name in ("resident", "admin", "business"):
        bot_payload = bots.get(bot_name) or {}
        missing = bot_payload.get("missing") or {}
        input_payload = bot_payload.get("input_flows") or {}
        lines.append(f"## {bot_name}")
        lines.append("")
        lines.append(
            f"- callbacks missing: eq={len(missing.get('eq') or [])}, "
            f"startswith={len(missing.get('startswith') or [])}, "
            f"regexp={len(missing.get('regexp') or [])}"
        )
        lines.append(
            f"- input missing: {len(input_payload.get('missing') or [])} / "
            f"{len(input_payload.get('required') or [])}"
        )

        eq_missing = [str(v) for v in (missing.get("eq") or [])]
        sw_missing = [str(v) for v in (missing.get("startswith") or [])]
        rg_missing = [str(v) for v in (missing.get("regexp") or [])]
        input_missing = [str(v) for v in (input_payload.get("missing") or [])]
        if eq_missing:
            lines.append(f"- missing eq: `{', '.join(eq_missing)}`")
        if sw_missing:
            lines.append(f"- missing startswith: `{', '.join(sw_missing)}`")
        if rg_missing:
            lines.append(f"- missing regexp: `{', '.join(rg_missing)}`")
        if input_missing:
            lines.append(f"- missing input flows: `{', '.join(input_missing)}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


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
            scenario_results, scenario_names_by_bot = await _run_scenarios(ctx)
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

            # Callback coverage telemetry (read-only subset for full-click roadmap).
            try:
                strict_default = str(os.getenv("TESTERBOT_CALLBACK_COVERAGE_STRICT", "1")).strip() == "1"
                strict = str(
                    os.getenv(
                        "TESTERBOT_FULL_COVERAGE_STRICT",
                        "1" if strict_default else "0",
                    )
                ).strip() == "1"
                callback_coverage_path = str(
                    os.getenv(
                        "TESTERBOT_CALLBACK_COVERAGE_PATH",
                        "/data/logs/testerbot_callback_coverage.json",
                    )
                ).strip()
                full_coverage_path = str(
                    os.getenv(
                        "TESTERBOT_FULL_COVERAGE_PATH",
                        "/data/logs/testerbot_full_coverage.json",
                    )
                ).strip()
                gap_report_path = str(
                    os.getenv(
                        "TESTERBOT_GAP_REPORT_PATH",
                        "/data/logs/testerbot_gap_report.md",
                    )
                ).strip()
                repo_root = Path(__file__).resolve().parents[1]
                inventory = filter_read_only_inventory(parse_callback_inventory(repo_root))
                coverage_lines: list[str] = []
                coverage_failed = False
                callback_inventory_total = 0
                callback_missing_total = 0
                coverage_payload: dict[str, object] = {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "strict_mode": strict,
                    "bots": {},
                }
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
                    callback_inventory_total += total_rules
                    callback_missing_total += missing_total
                    coverage_lines.append(
                        f"{bot_name}: seen={len(seen)} clicked={len(clicked)} inventory={total_rules} missing={missing_total}"
                    )
                    if missing_total > 0:
                        logger.warning(
                            "callback coverage missing for %s: eq=%s startswith=%s regexp=%s",
                            bot_name,
                            sorted(missing.get("eq", set())),
                            sorted(missing.get("startswith", set())),
                            sorted(missing.get("regexp", set())),
                        )
                    if strict and missing_total > 0:
                        coverage_failed = True
                        logger.error("callback coverage strict fail for %s", bot_name)
                    clicked_map = ctx.clicked_callback_scenarios.get(bot_name, {})
                    seen_map = ctx.seen_callback_scenarios.get(bot_name, {})
                    coverage_payload["bots"][bot_name] = {
                        "scenario_name": scenario_names_by_bot.get(bot_name, ""),
                        "seen": sorted(seen),
                        "clicked": sorted(clicked),
                        "callback_to_scenarios": {
                            "seen": {
                                key: sorted(values)
                                for key, values in sorted(seen_map.items())
                            },
                            "clicked": {
                                key: sorted(values)
                                for key, values in sorted(clicked_map.items())
                            },
                        },
                        "inventory": {
                            "eq": sorted(inv.get("eq", set())),
                            "startswith": sorted(inv.get("startswith", set())),
                            "regexp": sorted(inv.get("regexp", set())),
                        },
                        "missing": {
                            "eq": sorted(missing.get("eq", set())),
                            "startswith": sorted(missing.get("startswith", set())),
                            "regexp": sorted(missing.get("regexp", set())),
                        },
                        "stats": {
                            "seen": len(seen),
                            "clicked": len(clicked),
                            "inventory": total_rules,
                            "missing": missing_total,
                        },
                    }

                # Input-flow coverage (deterministic, read-only scope).
                input_required_total = 0
                input_missing_total = 0
                for bot_name in ("resident", "admin", "business"):
                    required = set(REQUIRED_INPUT_FLOWS.get(bot_name, set()))
                    observed = set(ctx.input_flows.get(bot_name, set()))
                    missing_input = sorted(required - observed)
                    input_required_total += len(required)
                    input_missing_total += len(missing_input)
                    bot_payload = coverage_payload["bots"].get(bot_name, {})
                    if isinstance(bot_payload, dict):
                        bot_payload["input_flows"] = {
                            "required": sorted(required),
                            "observed": sorted(observed),
                            "missing": missing_input,
                            "coverage_percent": (
                                100.0 if not required else round((len(required) - len(missing_input)) * 100.0 / len(required), 2)
                            ),
                        }
                        coverage_payload["bots"][bot_name] = bot_payload
                    if strict and missing_input:
                        coverage_failed = True
                        logger.error("input-flow coverage strict fail for %s: missing=%s", bot_name, missing_input)

                callback_covered_total = max(callback_inventory_total - callback_missing_total, 0)
                input_covered_total = max(input_required_total - input_missing_total, 0)
                coverage_payload["callback_inventory_total"] = callback_inventory_total
                coverage_payload["callback_covered_total"] = callback_covered_total
                coverage_payload["callback_uncovered_total"] = callback_missing_total
                coverage_payload["input_flows_total"] = input_required_total
                coverage_payload["input_flows_covered"] = input_covered_total
                coverage_payload["input_flows_missing_total"] = input_missing_total
                coverage_payload["input_flows_coverage_percent"] = (
                    100.0
                    if input_required_total == 0
                    else round(input_covered_total * 100.0 / input_required_total, 2)
                )

                logger.info("testerbot callback coverage (read-only): %s", " | ".join(coverage_lines))
                if callback_coverage_path:
                    _write_callback_coverage_report(callback_coverage_path, coverage_payload)
                    logger.info("testerbot callback coverage report written: %s", callback_coverage_path)
                if full_coverage_path:
                    _write_callback_coverage_report(full_coverage_path, coverage_payload)
                    logger.info("testerbot full coverage report written: %s", full_coverage_path)
                if gap_report_path:
                    _write_text_report(gap_report_path, _build_gap_report_md(coverage_payload))
                    logger.info("testerbot gap report written: %s", gap_report_path)
                if strict and coverage_failed:
                    scenario_results.append(
                        ScenarioReport(
                            name="testerbot_full_coverage_strict",
                            status="error",
                            duration_ms=0,
                            message=(
                                "missing full coverage in strict mode: "
                                f"callbacks_uncovered={callback_missing_total}, "
                                f"input_missing={input_missing_total}"
                            ),
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
