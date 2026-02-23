#!/usr/bin/env python3
"""
Smoke for adbot strong-signal guard.

Ensures matcher does not trigger on generic words only ("номер", "телефон"),
and still matches when domain-specific strong signals are present.
"""

from __future__ import annotations

import sys
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
    from adbot.matcher import analyze_intent_match, match_intent

    generic = analyze_intent_match(
        "Підкажіть номер і телефон, будь ласка",
        min_len=10,
        max_len=280,
        min_confidence=120,
    )
    _assert(generic.intent is None, "generic message must not match any intent")
    _assert(
        generic.reason in {"missing_strong_signal", "no_signal_candidates"},
        f"unexpected reason for generic message: {generic.reason}",
    )

    weak_light = analyze_intent_match(
        "Потрібен номер, бо в під'їзді щось не так",
        min_len=10,
        max_len=280,
        min_confidence=120,
    )
    _assert(weak_light.intent is None, "weak generic signal must not match")

    positive_electrician = match_intent(
        "Дайте номер електрика, будь ласка",
        min_len=10,
        max_len=280,
        min_confidence=120,
    )
    _assert(
        positive_electrician is not None and positive_electrician.code == "electrician",
        "electrician prompt with strong signal must match",
    )

    positive_light = match_intent(
        "Чи є світло в Ньюкасл?",
        min_len=10,
        max_len=280,
        min_confidence=120,
    )
    _assert(
        positive_light is not None and positive_light.code == "light_status",
        "light-status prompt with strong signal must match",
    )

    print("OK: adbot strong-signal guard smoke passed.")


if __name__ == "__main__":
    main()
