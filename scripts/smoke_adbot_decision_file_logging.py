#!/usr/bin/env python3
"""
Smoke for dedicated adbot decision log file wiring.

Ensures `configure_decision_logging()` creates and writes a separate
decision log file (in addition to regular adbot logs).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _bootstrap_imports() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))


def main() -> None:
    _bootstrap_imports()
    from adbot.audit import (
        _DECISION_HANDLER_MARKER,
        build_decision_payload,
        configure_decision_logging,
        decision_logger,
        log_decision,
    )

    with tempfile.TemporaryDirectory(prefix="adbot-decision-log-") as tmp:
        decision_path = Path(tmp) / "decisions.log"
        os.environ["ADBOT_DECISION_LOG_PATH"] = str(decision_path)
        os.environ["ADBOT_DECISION_LOG_MAX_BYTES"] = "2048"
        os.environ["ADBOT_DECISION_LOG_BACKUP_COUNT"] = "1"

        # Reset previously attached dedicated handlers to make this smoke deterministic.
        for handler in list(decision_logger.handlers):
            if getattr(handler, _DECISION_HANDLER_MARKER, False):
                decision_logger.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass

        configure_decision_logging()
        log_decision(
            build_decision_payload(
                chat_id=-1001,
                user_id=42,
                reason="file_smoke",
                message_text="test message",
                intent_code="electrician",
            )
        )

        for handler in list(decision_logger.handlers):
            try:
                handler.flush()
            except Exception:
                pass

        _assert(decision_path.exists(), f"decision log file not created: {decision_path}")
        content = decision_path.read_text(encoding="utf-8")
        _assert("adbot decision:" in content, "decision marker missing in decision log file")
        _assert("file_smoke" in content, "reason not found in decision log file")

    print("OK: adbot decision file logging smoke passed.")


if __name__ == "__main__":
    main()
