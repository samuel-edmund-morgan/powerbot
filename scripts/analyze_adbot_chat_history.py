#!/usr/bin/env python3
"""Wrapper to run adbot chat-history analyzer from src/tools."""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_imports() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))


def main() -> None:
    _bootstrap_imports()
    from tools.analyze_adbot_chat_history import main as tool_main

    tool_main()


if __name__ == "__main__":
    main()

