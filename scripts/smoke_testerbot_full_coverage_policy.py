#!/usr/bin/env python3
"""
Static smoke: testerbot full-coverage policy wiring.

Checks:
- `src/testerbot_main.py` writes `TESTERBOT_FULL_COVERAGE_PATH` artifact.
- `src/testerbot_main.py` writes `TESTERBOT_GAP_REPORT_PATH` report.
- strict gate emits `testerbot_full_coverage_strict` failure scenario.
- deploy_test includes runtime smoke for the full coverage artifact.
- deploy_test includes strict-negative smoke to verify fail-closed behavior.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TESTERBOT_MAIN = REPO_ROOT / "src" / "testerbot_main.py"
DEPLOY_TEST = REPO_ROOT / "scripts" / "deploy_test.sh"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    _assert(TESTERBOT_MAIN.exists(), f"file not found: {TESTERBOT_MAIN}")
    _assert(DEPLOY_TEST.exists(), f"file not found: {DEPLOY_TEST}")

    main_text = TESTERBOT_MAIN.read_text(encoding="utf-8")
    deploy_text = DEPLOY_TEST.read_text(encoding="utf-8")

    _assert(
        "TESTERBOT_FULL_COVERAGE_PATH" in main_text,
        "testerbot_main must write TESTERBOT_FULL_COVERAGE_PATH artifact",
    )
    _assert(
        "TESTERBOT_GAP_REPORT_PATH" in main_text,
        "testerbot_main must write TESTERBOT_GAP_REPORT_PATH report",
    )
    _assert(
        "testerbot_full_coverage_strict" in main_text,
        "testerbot_main strict gate must emit testerbot_full_coverage_strict scenario",
    )

    _assert(
        "smoke_testerbot_full_coverage_runtime.py" in deploy_text,
        "deploy_test.sh must run smoke_testerbot_full_coverage_runtime.py",
    )
    _assert(
        "smoke_testerbot_full_coverage_strict_negative.py" in deploy_text,
        "deploy_test.sh must run smoke_testerbot_full_coverage_strict_negative.py",
    )

    print("OK: testerbot full-coverage policy smoke passed.")


if __name__ == "__main__":
    main()
