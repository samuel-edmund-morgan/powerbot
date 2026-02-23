#!/usr/bin/env python3
"""
Static policy smoke:
testerbot callback coverage strict-gate must stay ON by default in CI.

Checks:
- src/testerbot_main.py uses default "1" for TESTERBOT_CALLBACK_COVERAGE_STRICT
- src/testerbot_main.py writes callback coverage map to TESTERBOT_CALLBACK_COVERAGE_PATH
- .env.example defines TESTERBOT_CALLBACK_COVERAGE_STRICT=1
- .env.example defines TESTERBOT_CALLBACK_COVERAGE_PATH
"""

from __future__ import annotations

from pathlib import Path
import re


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    testerbot_main = (repo_root / "src" / "testerbot_main.py").read_text(encoding="utf-8")
    env_example = (repo_root / ".env.example").read_text(encoding="utf-8")

    _assert(
        'os.getenv("TESTERBOT_CALLBACK_COVERAGE_STRICT", "1")' in testerbot_main,
        "testerbot_main.py must default TESTERBOT_CALLBACK_COVERAGE_STRICT to \"1\"",
    )
    _assert(
        'os.getenv(\n                        "TESTERBOT_CALLBACK_COVERAGE_PATH",' in testerbot_main
        or 'os.getenv("TESTERBOT_CALLBACK_COVERAGE_PATH"' in testerbot_main,
        "testerbot_main.py must read TESTERBOT_CALLBACK_COVERAGE_PATH",
    )
    _assert(
        "_write_callback_coverage_report" in testerbot_main,
        "testerbot_main.py must persist callback coverage report via _write_callback_coverage_report",
    )

    match = re.search(
        r"^TESTERBOT_CALLBACK_COVERAGE_STRICT\s*=\s*([^\n#]+)\s*$",
        env_example,
        flags=re.MULTILINE,
    )
    _assert(match is not None, ".env.example must define TESTERBOT_CALLBACK_COVERAGE_STRICT")
    value = match.group(1).strip().strip('"').strip("'")
    _assert(value == "1", ".env.example TESTERBOT_CALLBACK_COVERAGE_STRICT must be 1")

    match_path = re.search(
        r"^TESTERBOT_CALLBACK_COVERAGE_PATH\s*=\s*([^\n#]+)\s*$",
        env_example,
        flags=re.MULTILINE,
    )
    _assert(match_path is not None, ".env.example must define TESTERBOT_CALLBACK_COVERAGE_PATH")
    path_value = match_path.group(1).strip().strip('"').strip("'")
    _assert(
        path_value == "/data/logs/testerbot_callback_coverage.json",
        ".env.example TESTERBOT_CALLBACK_COVERAGE_PATH must be /data/logs/testerbot_callback_coverage.json",
    )

    print("OK: testerbot callback-coverage strict policy smoke passed.")


if __name__ == "__main__":
    main()
