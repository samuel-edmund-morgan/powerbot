#!/usr/bin/env python3
"""
Static smoke: testerbot must be isolated to test-only compose override.

Policy:
- base docker-compose.yml must not declare service `testerbot`
- docker-compose.testerbot.yml must declare service `testerbot`
"""

from __future__ import annotations

from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _has_service_block(text: str, service_name: str) -> bool:
    needle = f"  {service_name}:"
    return needle in text


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    base = (repo_root / "docker-compose.yml").read_text(encoding="utf-8")
    override = (repo_root / "docker-compose.testerbot.yml").read_text(encoding="utf-8")

    _assert(
        not _has_service_block(base, "testerbot"),
        "docker-compose.yml must not contain testerbot service",
    )
    _assert(
        _has_service_block(override, "testerbot"),
        "docker-compose.testerbot.yml must contain testerbot service",
    )

    print("OK: testerbot compose isolation smoke passed.")


if __name__ == "__main__":
    main()

