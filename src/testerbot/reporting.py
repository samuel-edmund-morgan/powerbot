"""Reporting helpers for testerbot execution results."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from xml.etree.ElementTree import Element, SubElement, tostring
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ScenarioReport:
    name: str
    status: str
    duration_ms: int
    message: str


@dataclass
class TesterbotRunReport:
    started_at: str
    finished_at: str
    total: int
    passed: int
    failed: int
    scenarios: list[ScenarioReport]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_empty_report() -> TesterbotRunReport:
    ts = _utcnow_iso()
    return TesterbotRunReport(
        started_at=ts,
        finished_at=ts,
        total=0,
        passed=0,
        failed=0,
        scenarios=[],
    )


def write_report(report: TesterbotRunReport, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(report), f, ensure_ascii=False, indent=2)


def mark_finished(
    report: TesterbotRunReport,
    scenario_results: list[ScenarioReport],
) -> TesterbotRunReport:
    passed = [s for s in scenario_results if s.status == "ok"]
    failed = [s for s in scenario_results if s.status != "ok"]
    report.finished_at = _utcnow_iso()
    report.total = len(scenario_results)
    report.passed = len(passed)
    report.failed = len(failed)
    report.scenarios = scenario_results
    return report


def first_error_message(report: TesterbotRunReport) -> str | None:
    for scenario in report.scenarios:
        if scenario.status != "ok":
            return scenario.message
    return None


def format_text_summary(report: TesterbotRunReport) -> str:
    return (
        f"testerbot summary: total={report.total} "
        f"passed={report.passed} failed={report.failed}"
    )


def write_junit_xml(report: TesterbotRunReport, path: str) -> None:
    testsuite = Element("testsuite")
    testsuite.set("name", "testerbot")
    testsuite.set("tests", str(report.total))
    testsuite.set("failures", str(report.failed))
    testsuite.set("timestamp", report.finished_at)

    for scenario in report.scenarios:
        case = SubElement(testsuite, "testcase")
        case.set("name", scenario.name)
        if scenario.status != "ok":
            failure = SubElement(case, "failure")
            failure.set("message", scenario.status)
            failure.text = scenario.message

    content = tostring(testsuite, encoding="unicode")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
