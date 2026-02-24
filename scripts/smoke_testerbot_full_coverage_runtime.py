#!/usr/bin/env python3
"""
Runtime smoke: validate testerbot full coverage artifact.

Checks:
- artifact exists and has expected top-level totals;
- callback_uncovered_total == 0;
- input_flows_missing_total == 0 and input_flows_coverage_percent == 100;
- per-bot input flow blocks exist.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate testerbot full-coverage runtime artifact")
    parser.add_argument(
        "--coverage-file",
        default="/opt/powerbot-test/logs/testerbot_full_coverage.json",
        help="Path to testerbot full coverage json",
    )
    args = parser.parse_args()

    path = Path(args.coverage_file)
    _assert(path.exists(), f"coverage artifact not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    _assert(isinstance(payload, dict), "coverage payload must be an object")
    _assert("bots" in payload and isinstance(payload["bots"], dict), "coverage payload missing `bots` object")

    callback_uncovered_total = int(payload.get("callback_uncovered_total") or 0)
    input_missing_total = int(payload.get("input_flows_missing_total") or 0)
    input_coverage = float(payload.get("input_flows_coverage_percent") or 0.0)

    _assert(callback_uncovered_total == 0, f"callback_uncovered_total must be 0, got {callback_uncovered_total}")
    _assert(input_missing_total == 0, f"input_flows_missing_total must be 0, got {input_missing_total}")
    _assert(input_coverage >= 100.0, f"input_flows_coverage_percent must be 100, got {input_coverage}")

    for bot_name in ("resident", "admin", "business"):
        bot_payload = payload["bots"].get(bot_name)
        _assert(isinstance(bot_payload, dict), f"missing bot payload: {bot_name}")
        input_flows = bot_payload.get("input_flows")
        _assert(isinstance(input_flows, dict), f"{bot_name}: missing input_flows block")
        for key in ("required", "observed", "missing", "coverage_percent"):
            _assert(key in input_flows, f"{bot_name}: input_flows missing key `{key}`")

    print("OK: testerbot full coverage runtime smoke passed.")


if __name__ == "__main__":
    main()

