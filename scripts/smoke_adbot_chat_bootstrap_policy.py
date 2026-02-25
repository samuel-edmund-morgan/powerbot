#!/usr/bin/env python3
"""
Policy smoke for adbot chat-id bootstrap wiring in test deploy.

Checks:
- deploy_test.sh invokes bootstrap_adbot_chat_ids.py before Telethon preflight;
- .env.example exposes chat-title bootstrap vars.
"""

from __future__ import annotations

from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    deploy_test = repo_root / "scripts" / "deploy_test.sh"
    env_example = repo_root / ".env.example"
    bootstrap_script = repo_root / "scripts" / "bootstrap_adbot_chat_ids.py"

    _assert(deploy_test.exists(), "scripts/deploy_test.sh not found")
    _assert(env_example.exists(), ".env.example not found")
    _assert(bootstrap_script.exists(), "scripts/bootstrap_adbot_chat_ids.py not found")

    deploy_text = deploy_test.read_text(encoding="utf-8")
    env_text = env_example.read_text(encoding="utf-8")

    marker_bootstrap = "bootstrap_adbot_chat_ids.py"
    marker_preflight = "smoke_telethon_env_contract.py"
    _assert(marker_bootstrap in deploy_text, "deploy_test missing adbot chat bootstrap invocation")
    _assert(marker_preflight in deploy_text, "deploy_test missing Telethon preflight invocation")
    _assert(
        deploy_text.find(marker_bootstrap) < deploy_text.find(marker_preflight),
        "adbot chat bootstrap must run before Telethon preflight",
    )

    required_env_keys = (
        "ADBOT_SOURCE_CHAT_TITLES=",
        "ADBOT_INTERNAL_CHAT_TITLE=",
        "ADBOT_PAIR_1_SOURCE_CHAT_TITLE=",
        "ADBOT_PAIR_1_INTERNAL_CHAT_TITLE=",
        "ADBOT_E2E_SOURCE_CHAT_TITLE=",
        "ADBOT_E2E_INTERNAL_CHAT_TITLE=",
    )
    for key in required_env_keys:
        _assert(key in env_text, f".env.example missing key: {key}")

    print("OK: adbot chat bootstrap policy smoke passed.")


if __name__ == "__main__":
    main()
