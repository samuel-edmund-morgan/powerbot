"""Rule-based matcher for group messages."""

from __future__ import annotations

import re

from adbot.intents import INTENTS, Intent


def normalize(text: str) -> str:
    return (text or "").strip().lower()


def _match_signals(text_norm: str, intent: Intent) -> int:
    score = 0
    for token in intent.keywords:
        # partial substring match allows flexible Ukrainian variants.
        if token in text_norm:
            score += 1
    return score


def match_intent(
    text: str,
    *,
    min_len: int,
    max_len: int,
    min_confidence: int,
) -> Intent | None:
    """
    Return best matching intent for short, high-signal messages.
    Uses heuristic anti-false-positive guard for long texts.
    """
    norm = normalize(text)
    if len(norm) < min_len or len(norm) > max_len:
        return None

    tokens = [t for t in re.split(r"\s+", norm) if t]
    if not tokens:
        return None

    best: tuple[int, Intent] | None = None
    for intent in INTENTS:
        score = _match_signals(norm, intent)
        if score < intent.required_signals:
            continue
        # density guard: avoid accidental matches on very long texts.
        # Convert ratio to pseudo-confidence in [0..1000].
        confidence = int((score / max(len(tokens), 1)) * 1000)
        if confidence < min_confidence:
            continue
        if best is None or confidence > best[0]:
            best = (confidence, intent)

    if best is None:
        return None
    return best[1]
