#!/usr/bin/env python3
"""
Smoke for adbot matcher diagnostics contract.

Verifies analyze_intent_match reason codes and key fields:
- below_min_len
- above_max_len
- no_signal_candidates
- below_min_confidence
- matched
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

    short = analyze_intent_match("привіт", min_len=10, max_len=280, min_confidence=120)
    _assert(short.intent is None, "short input should not match")
    _assert(short.reason == "below_min_len", f"unexpected short reason: {short.reason}")

    long_text = ("слово " * 400).strip()
    long_diag = analyze_intent_match(long_text, min_len=10, max_len=280, min_confidence=120)
    _assert(long_diag.intent is None, "too long input should not match")
    _assert(long_diag.reason == "above_max_len", f"unexpected long reason: {long_diag.reason}")

    no_signal = analyze_intent_match(
        "Потрібно обговорити ремонт під'їзду та закупівлю матеріалів",
        min_len=10,
        max_len=280,
        min_confidence=120,
    )
    _assert(no_signal.intent is None, "no-signal text should not match")
    _assert(no_signal.reason == "no_signal_candidates", f"unexpected no-signal reason: {no_signal.reason}")

    low_conf_text = ("слова " * 18) + " номер електрика "
    low_conf = analyze_intent_match(low_conf_text, min_len=10, max_len=280, min_confidence=120)
    _assert(low_conf.intent is None, "low-confidence text should not match")
    _assert(low_conf.reason == "below_min_confidence", f"unexpected low-confidence reason: {low_conf.reason}")
    _assert(low_conf.best_intent is not None, "best_intent should be present for below_min_confidence")
    _assert(low_conf.best_confidence is not None, "best_confidence should be present for below_min_confidence")

    matched = analyze_intent_match(
        "Дайте номер електрика, будь ласка",
        min_len=10,
        max_len=280,
        min_confidence=120,
    )
    _assert(matched.intent is not None, "expected positive match")
    _assert(matched.reason == "matched", f"unexpected match reason: {matched.reason}")
    _assert(matched.best_intent == "electrician", f"unexpected best_intent: {matched.best_intent}")
    _assert(match_intent("Дайте номер електрика, будь ласка", min_len=10, max_len=280, min_confidence=120) is not None,
            "match_intent wrapper must remain compatible")

    print("OK: adbot matcher diagnostics smoke passed.")


if __name__ == "__main__":
    main()
