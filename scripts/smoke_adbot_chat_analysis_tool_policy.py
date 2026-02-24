#!/usr/bin/env python3
"""
Policy smoke for adbot chat-history analyzer tool wiring.

Ensures:
- container-available tool exists at src/tools/analyze_adbot_chat_history.py;
- scripts/analyze_adbot_chat_history.py is a thin wrapper to src/tools tool;
- README documents docker-based execution path.
"""

from __future__ import annotations

from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    tool_path = repo_root / "src" / "tools" / "analyze_adbot_chat_history.py"
    wrapper_path = repo_root / "scripts" / "analyze_adbot_chat_history.py"
    readme_path = repo_root / "README.md"

    _assert(tool_path.exists(), "missing tool: src/tools/analyze_adbot_chat_history.py")
    _assert(wrapper_path.exists(), "missing wrapper: scripts/analyze_adbot_chat_history.py")
    _assert(readme_path.exists(), "missing README.md")

    tool_src = tool_path.read_text(encoding="utf-8")
    wrapper_src = wrapper_path.read_text(encoding="utf-8")
    readme_src = readme_path.read_text(encoding="utf-8")

    _assert(
        "from adbot.matcher import analyze_intent_match" in tool_src,
        "tool must import matcher diagnostics runtime",
    )
    _assert(
        "--chat-title" in tool_src and "--months" in tool_src,
        "tool argparse contract missing required flags",
    )
    _assert(
        "from tools.analyze_adbot_chat_history import main as tool_main" in wrapper_src,
        "scripts wrapper must delegate to src/tools analyzer",
    )
    _assert(
        "/app/src/tools/analyze_adbot_chat_history.py" in readme_src,
        "README must document docker/container run for chat analyzer tool",
    )

    print("OK: adbot chat analysis tool policy smoke passed.")


if __name__ == "__main__":
    main()

