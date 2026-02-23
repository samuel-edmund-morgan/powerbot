#!/usr/bin/env python3
"""
Contract smoke for scripts/smoke_telethon_env_contract.py.

Validates key fail-fast cases:
- short/invalid StringSession is rejected when runner is enabled;
- non-numeric chat ids are rejected for adbot/adbot_e2e;
- valid-looking inputs pass.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _run_preflight(env_text: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    preflight = repo_root / "scripts" / "smoke_telethon_env_contract.py"
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as fh:
        fh.write(env_text)
        env_path = fh.name
    try:
        return subprocess.run(
            [sys.executable, str(preflight), "--env-file", env_path],
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        Path(env_path).unlink(missing_ok=True)


def main() -> None:
    long_session = "A" * 120
    common = (
        "TELETHON_API_ID=33083266\n"
        "TELETHON_API_HASH=87fe44a26a1a986054273f1d58ab7f3f\n"
        "TESTERBOT_TARGET_POWERBOT_USERNAME=bot_a\n"
        "TESTERBOT_TARGET_ADMINBOT_USERNAME=bot_b\n"
        "TESTERBOT_TARGET_BUSINESSBOT_USERNAME=bot_c\n"
        "ADBOT_TARGET_POWERBOT_USERNAME=bot_a\n"
    )

    # 1) Disabled runners should pass even without sessions/chat ids.
    res_disabled = _run_preflight(common + "TESTERBOT_ENABLED=0\nADBOT_ENABLED=0\nADBOT_E2E_ENABLED=0\n")
    _assert(res_disabled.returncode == 0, f"disabled env must pass, got={res_disabled.stdout}{res_disabled.stderr}")

    # 2) Testerbot enabled with short token must fail.
    res_bad_tester = _run_preflight(common + "TESTERBOT_ENABLED=1\nTESTERBOT_STRING_SESSION=short-token\n")
    _assert(res_bad_tester.returncode != 0, "short testerbot session must fail")
    _assert(
        "does not look like a valid Telethon StringSession" in (res_bad_tester.stdout + res_bad_tester.stderr),
        "expected invalid StringSession diagnostics for testerbot",
    )

    # 3) Adbot enabled (non-test mode) must enforce numeric chat ids.
    res_bad_adbot = _run_preflight(
        common
        + f"ADBOT_ENABLED=1\nADBOT_TEST_MODE=0\nADBOT_STRING_SESSION={long_session}\n"
        + "ADBOT_SOURCE_CHAT_IDS=abc,-100111\nADBOT_INTERNAL_CHAT_ID=chat-id\n"
    )
    _assert(res_bad_adbot.returncode != 0, "non-numeric adbot chat ids must fail")
    _assert(
        "contains non-numeric chat_id values" in (res_bad_adbot.stdout + res_bad_adbot.stderr),
        "expected non-numeric source chat ids diagnostics",
    )
    _assert(
        "must be numeric chat_id" in (res_bad_adbot.stdout + res_bad_adbot.stderr),
        "expected numeric internal chat id diagnostics",
    )

    # 4) Adbot E2E enabled must enforce valid-looking session + numeric source id.
    res_bad_adbot_e2e = _run_preflight(
        common + "ADBOT_E2E_ENABLED=1\nADBOT_E2E_DRIVER_STRING_SESSION=too_short\nADBOT_E2E_SOURCE_CHAT_ID=group\n"
    )
    _assert(res_bad_adbot_e2e.returncode != 0, "invalid adbot e2e contract must fail")

    # 4b) If adbot and e2e-driver sessions are identical, self-outgoing guard must be enabled.
    res_same_session_guard = _run_preflight(
        common
        + "ADBOT_ENABLED=1\nADBOT_TEST_MODE=0\n"
        + f"ADBOT_STRING_SESSION={long_session}\n"
        + "ADBOT_SOURCE_CHAT_IDS=-100111\nADBOT_INTERNAL_CHAT_ID=-100333\n"
        + "ADBOT_E2E_ENABLED=1\n"
        + f"ADBOT_E2E_DRIVER_STRING_SESSION={long_session}\n"
        + "ADBOT_E2E_SOURCE_CHAT_ID=-100111\n"
    )
    _assert(
        res_same_session_guard.returncode != 0,
        "same adbot/e2e sessions without self-outgoing guard must fail",
    )
    _assert(
        "ADBOT_ALLOW_SELF_OUTGOING_E2E=1" in (res_same_session_guard.stdout + res_same_session_guard.stderr),
        "expected same-session guard diagnostics",
    )

    # 5) Valid-looking runner env should pass.
    res_ok = _run_preflight(
        common
        + "TESTERBOT_ENABLED=1\n"
        + f"TESTERBOT_STRING_SESSION={long_session}\n"
        + "ADBOT_ENABLED=1\nADBOT_TEST_MODE=0\n"
        + f"ADBOT_STRING_SESSION={long_session}\n"
        + "ADBOT_ALLOW_SELF_OUTGOING_E2E=1\n"
        + "ADBOT_SELF_OUTGOING_PREFIX=[E2E]\n"
        + "ADBOT_SOURCE_CHAT_IDS=-100111,-100222\nADBOT_INTERNAL_CHAT_ID=-100333\n"
        + "ADBOT_E2E_ENABLED=1\n"
        + f"ADBOT_E2E_DRIVER_STRING_SESSION={long_session}\n"
        + "ADBOT_E2E_PROMPT_PREFIX=[E2E] \n"
        + "ADBOT_E2E_SOURCE_CHAT_ID=-100111\n"
    )
    _assert(res_ok.returncode == 0, f"valid-looking env must pass, got={res_ok.stdout}{res_ok.stderr}")

    print("OK: Telethon env preflight contract cases passed.")


if __name__ == "__main__":
    main()
