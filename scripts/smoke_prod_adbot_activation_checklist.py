#!/usr/bin/env python3
"""
Smoke test for prod adbot activation checklist.

Verifies:
- valid env passes;
- missing required chat IDs fails with clear output.
- valid pair-mode env passes;
- invalid pair-mode env fails with clear output.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKLIST_SCRIPT = REPO_ROOT / "scripts" / "prod_adbot_activation_checklist.py"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _write_env(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _run(env_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKLIST_SCRIPT), "--env-file", str(env_file)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env={**os.environ},
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="smoke_prod_adbot_checklist_") as tmp:
        tmp_dir = Path(tmp)
        valid_env = tmp_dir / "valid.env"
        invalid_env = tmp_dir / "invalid.env"

        _write_env(
            valid_env,
            "\n".join(
                [
                    "ADBOT_ENABLED=1",
                    "ADBOT_TEST_MODE=0",
                    "TELETHON_API_ID=33083266",
                    "TELETHON_API_HASH=87fe44a26a1a986054273f1d58ab7f3f",
                    "ADBOT_STRING_SESSION=1Aabcdefghijklmnopqrstuvw",
                    "ADBOT_TARGET_POWERBOT_USERNAME=TestNaButlerBot",
                    "ADBOT_SOURCE_CHAT_IDS=-100123,-100124",
                    "ADBOT_INTERNAL_CHAT_ID=-100125",
                    "",
                ]
            ),
        )
        ok = _run(valid_env)
        _assert(ok.returncode == 0, f"valid env must pass, rc={ok.returncode}, out={ok.stdout}, err={ok.stderr}")
        _assert(
            "OK: prod adbot activation checklist passed." in ok.stdout,
            f"success marker missing: {ok.stdout}",
        )

        _write_env(
            invalid_env,
            "\n".join(
                [
                    "ADBOT_ENABLED=1",
                    "ADBOT_TEST_MODE=0",
                    "TELETHON_API_ID=33083266",
                    "TELETHON_API_HASH=87fe44a26a1a986054273f1d58ab7f3f",
                    "ADBOT_STRING_SESSION=1Aabcdefghijklmnopqrstuvw",
                    "ADBOT_TARGET_POWERBOT_USERNAME=TestNaButlerBot",
                    "ADBOT_SOURCE_CHAT_IDS=",
                    "ADBOT_INTERNAL_CHAT_ID=",
                    "",
                ]
            ),
        )
        bad = _run(invalid_env)
        _assert(bad.returncode != 0, "invalid env must fail")
        _assert("ADBOT_SOURCE_CHAT_IDS" in bad.stdout, f"missing source id error in output: {bad.stdout}")
        _assert("ADBOT_INTERNAL_CHAT_ID" in bad.stdout, f"missing internal id error in output: {bad.stdout}")

        valid_pair_env = tmp_dir / "valid_pair.env"
        invalid_pair_env = tmp_dir / "invalid_pair.env"

        _write_env(
            valid_pair_env,
            "\n".join(
                [
                    "ADBOT_ENABLED=1",
                    "ADBOT_TEST_MODE=0",
                    "TELETHON_API_ID=33083266",
                    "TELETHON_API_HASH=87fe44a26a1a986054273f1d58ab7f3f",
                    "ADBOT_STRING_SESSION=1Aabcdefghijklmnopqrstuvw",
                    "ADBOT_TARGET_POWERBOT_USERNAME=TestNaButlerBot",
                    "ADBOT_PAIR_COUNT=2",
                    "ADBOT_PAIR_1_SOURCE_CHAT_ID=-100123",
                    "ADBOT_PAIR_1_INTERNAL_CHAT_ID=-100223",
                    "ADBOT_PAIR_1_SENSOR_UUID=esp32-one",
                    "ADBOT_PAIR_1_FALLBACK_BUILDING_ID=1",
                    "ADBOT_PAIR_1_FALLBACK_SECTION_ID=2",
                    "ADBOT_PAIR_1_REPLY_COOLDOWN_SEC=10800",
                    "ADBOT_PAIR_2_SOURCE_CHAT_ID=-100124",
                    "ADBOT_PAIR_2_INTERNAL_CHAT_ID=-100224",
                    "ADBOT_PAIR_2_SENSOR_UUID=esp32-two",
                    "ADBOT_PAIR_2_FALLBACK_BUILDING_ID=2",
                    "ADBOT_PAIR_2_FALLBACK_SECTION_ID=1",
                    "",
                ]
            ),
        )
        ok_pair = _run(valid_pair_env)
        _assert(
            ok_pair.returncode == 0,
            f"valid pair env must pass, rc={ok_pair.returncode}, out={ok_pair.stdout}, err={ok_pair.stderr}",
        )
        _assert(
            "[PASS] ADBOT_ROUTING_MODE: pair" in ok_pair.stdout,
            f"pair routing mode marker missing: {ok_pair.stdout}",
        )

        _write_env(
            invalid_pair_env,
            "\n".join(
                [
                    "ADBOT_ENABLED=1",
                    "ADBOT_TEST_MODE=0",
                    "TELETHON_API_ID=33083266",
                    "TELETHON_API_HASH=87fe44a26a1a986054273f1d58ab7f3f",
                    "ADBOT_STRING_SESSION=1Aabcdefghijklmnopqrstuvw",
                    "ADBOT_TARGET_POWERBOT_USERNAME=TestNaButlerBot",
                    "ADBOT_PAIR_COUNT=1",
                    "ADBOT_PAIR_1_SOURCE_CHAT_ID=-100123",
                    "ADBOT_PAIR_1_INTERNAL_CHAT_ID=",
                    "ADBOT_PAIR_1_SENSOR_UUID=",
                    "ADBOT_PAIR_1_FALLBACK_BUILDING_ID=0",
                    "ADBOT_PAIR_1_FALLBACK_SECTION_ID=1",
                    "",
                ]
            ),
        )
        bad_pair = _run(invalid_pair_env)
        _assert(bad_pair.returncode != 0, "invalid pair env must fail")
        _assert("ADBOT_PAIR_1_INTERNAL_CHAT_ID" in bad_pair.stdout, f"missing pair error: {bad_pair.stdout}")
        _assert("ADBOT_PAIR_1_SENSOR_UUID" in bad_pair.stdout, f"missing pair error: {bad_pair.stdout}")

    print("OK: smoke_prod_adbot_activation_checklist passed.")


if __name__ == "__main__":
    main()
