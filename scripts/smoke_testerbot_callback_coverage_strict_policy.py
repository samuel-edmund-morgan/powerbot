#!/usr/bin/env python3
"""
Static policy smoke:
testerbot callback coverage strict-gate must stay ON by default in CI.

Checks:
- src/testerbot_main.py uses default "1" for TESTERBOT_CALLBACK_COVERAGE_STRICT
- .env.example defines TESTERBOT_CALLBACK_COVERAGE_STRICT=1
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

    match = re.search(
        r"^TESTERBOT_CALLBACK_COVERAGE_STRICT\s*=\s*([^\n#]+)\s*$",
        env_example,
        flags=re.MULTILINE,
    )
    _assert(match is not None, ".env.example must define TESTERBOT_CALLBACK_COVERAGE_STRICT")
    value = match.group(1).strip().strip('"').strip("'")
    _assert(value == "1", ".env.example TESTERBOT_CALLBACK_COVERAGE_STRICT must be 1")

    print("OK: testerbot callback-coverage strict policy smoke passed.")


if __name__ == "__main__":
    main()
