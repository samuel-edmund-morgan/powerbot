#!/usr/bin/env python3
"""
Unit-like matcher smoke for adbot intents.

Covers positive/negative/edge cases for rule-based matching.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _load_matcher():
    # Make project modules importable when script is executed with plain python.
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))

    from adbot.matcher import analyze_intent_match, match_intent

    return match_intent, analyze_intent_match


def main() -> None:
    match_intent, analyze_intent_match = _load_matcher()

    tests = [
        # Positive
        ("Дайте номер електрика, будь ласка", "electrician"),
        ("Де знайти сантехніка? Потрібен номер", "plumber"),
        ("Чи є світло в Ньюкасл сьогодні?", "light_status"),
        ("Есть свет?", "light_status"),
        ("А є світло?", "light_status"),
        ("Дайте номер охорони, будь ласка", "security"),
        ("Де номер паркінгу для авто?", "parking"),
        ("Де отримати перепустку або пропуск на авто?", "car_pass"),
        ("Де оформити перепустку в паркінг?", "car_pass"),
        ("Дайте номер диспетчера ліфтів", "elevator"),
        ("Питання по нарахуванню, контакти бухгалтерії", "accounting"),

        # Negative
        ("Привіт, я перегляну графік для зустрічі о 18:00", None),
        (
            "А тепер про те, що світло відсутнє, але в нас теж відсутній трафарет і ми сьогодні купуємо новий...",
            None,
        ),
        ("А ще я замовив квіти", None),

        # Length guard
        ("привіт", None),
        ("світло", None),  # too short

        # Anti-false-positive boundary: one signal in very long text.
        ("Слова " * 130 + "світло", None),

        # Confidence guard: long text with many weak generic words + one signal.
        (
            "У цьому чаті ми обговорюємо новий ремонт котельні, питання по договору з підрядником "
            "та загалом організаційні моменти квартири, нічого про електрика чи службу не питаємо. "
            "Останнє слово: світло.",
            None,
        ),
    ]

    for text, expected_code in tests:
        intent = match_intent(text, min_len=10, max_len=280, min_confidence=120)
        matched = intent.code if intent else None
        _assert(
            matched == expected_code,
            f"Unexpected match result for: {text!r}. expected={expected_code}, got={matched}",
        )

    # Deterministic boundary: weak single-signal long text should stay unmatched.
    boundary = match_intent(
        "Це дуже довге технічне повідомлення про ремонт під'їзду, замок, чергову перевірку без ключових слів про людей.",
        min_len=10,
        max_len=280,
        min_confidence=120,
    )
    _assert(
        boundary is None,
        "Long non-target text should not trigger matcher (anti-false-positive guard).",
    )

    # Runtime regression: even with stricter min_len, short RU light questions must match.
    strict_runtime = analyze_intent_match("Есть ли дома свет?", min_len=14, max_len=280, min_confidence=120)
    _assert(
        strict_runtime.intent is not None and strict_runtime.intent.code == "light_status",
        f"Strict runtime short-RU query should match light_status, got={strict_runtime}",
    )

    strict_runtime_short = analyze_intent_match("Свет есть?", min_len=14, max_len=280, min_confidence=120)
    _assert(
        strict_runtime_short.intent is not None and strict_runtime_short.intent.code == "light_status",
        f"Strict runtime short-RU query should match light_status, got={strict_runtime_short}",
    )

    print("OK: adbot matcher smoke passed.")


if __name__ == "__main__":
    main()
