#!/usr/bin/env python3
"""
Negative smoke: strict full-coverage gate must fail on uncovered payload.

Creates a temporary malformed coverage artifact (with uncovered callbacks/input flows)
and verifies that `smoke_testerbot_full_coverage_runtime.py` exits non-zero.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SMOKE = REPO_ROOT / "scripts" / "smoke_testerbot_full_coverage_runtime.py"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    _assert(RUNTIME_SMOKE.exists(), f"runtime smoke not found: {RUNTIME_SMOKE}")

    payload = {
        "callback_inventory_total": 10,
        "callback_covered_total": 9,
        "callback_uncovered_total": 1,
        "input_flows_total": 4,
        "input_flows_covered": 3,
        "input_flows_missing_total": 1,
        "input_flows_coverage_percent": 75.0,
        "bots": {
            "resident": {
                "input_flows": {
                    "required": ["command:/start", "text:search_keyword"],
                    "observed": ["command:/start"],
                    "missing": ["text:search_keyword"],
                    "coverage_percent": 50.0,
                }
            },
            "admin": {
                "input_flows": {
                    "required": ["command:/start"],
                    "observed": ["command:/start"],
                    "missing": [],
                    "coverage_percent": 100.0,
                }
            },
            "business": {
                "input_flows": {
                    "required": ["command:/start"],
                    "observed": ["command:/start"],
                    "missing": [],
                    "coverage_percent": 100.0,
                }
            },
        },
    }

    with tempfile.TemporaryDirectory(prefix="testerbot-full-coverage-negative-") as tmp_dir:
        coverage_path = Path(tmp_dir) / "bad_full_coverage.json"
        coverage_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, str(RUNTIME_SMOKE), "--coverage-file", str(coverage_path)],
            capture_output=True,
            text=True,
            check=False,
        )

        _assert(
            proc.returncode != 0,
            "strict full-coverage runtime smoke must fail for malformed coverage payload",
        )
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        combined = f"{stdout}\n{stderr}".strip()
        _assert(
            "callback_uncovered_total must be 0" in combined
            or "input_flows_missing_total must be 0" in combined,
            "expected strict-fail reason is missing in runtime smoke output",
        )

    print("OK: testerbot full coverage strict-negative smoke passed.")


if __name__ == "__main__":
    main()

