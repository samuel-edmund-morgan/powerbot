"""Rule-based matcher for group messages."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class MatchDiagnostics:
    intent: Intent | None
    reason: str
    text_len: int
    token_count: int
    best_intent: str | None = None
    best_confidence: int | None = None
    best_signals: int | None = None


def _confidence(score: int, token_count: int) -> int:
    # Pseudo-confidence in [0..1000] based on keyword density.
    return int((int(score) / max(int(token_count), 1)) * 1000)


def analyze_intent_match(
    text: str,
    *,
    min_len: int,
    max_len: int,
    min_confidence: int,
) -> MatchDiagnostics:
    """Analyze text against adbot intents with detailed diagnostics."""
    norm = normalize(text)
    text_len = len(norm)
    if text_len < min_len:
        return MatchDiagnostics(intent=None, reason="below_min_len", text_len=text_len, token_count=0)
    if text_len > max_len:
        return MatchDiagnostics(intent=None, reason="above_max_len", text_len=text_len, token_count=0)

    tokens = [t for t in re.split(r"\s+", norm) if t]
    token_count = len(tokens)
    if token_count == 0:
        return MatchDiagnostics(intent=None, reason="no_tokens", text_len=text_len, token_count=0)

    best_candidate: tuple[int, int, Intent] | None = None
    for intent in INTENTS:
        score = _match_signals(norm, intent)
        if score < intent.required_signals:
            continue
        confidence = _confidence(score, token_count)
        if best_candidate is None or confidence > best_candidate[0]:
            best_candidate = (confidence, score, intent)

    if best_candidate is None:
        return MatchDiagnostics(
            intent=None,
            reason="no_signal_candidates",
            text_len=text_len,
            token_count=token_count,
        )

    best_confidence, best_signals, best_intent = best_candidate
    if best_confidence < min_confidence:
        return MatchDiagnostics(
            intent=None,
            reason="below_min_confidence",
            text_len=text_len,
            token_count=token_count,
            best_intent=best_intent.code,
            best_confidence=best_confidence,
            best_signals=best_signals,
        )

    return MatchDiagnostics(
        intent=best_intent,
        reason="matched",
        text_len=text_len,
        token_count=token_count,
        best_intent=best_intent.code,
        best_confidence=best_confidence,
        best_signals=best_signals,
    )


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
    return analyze_intent_match(
        text,
        min_len=min_len,
        max_len=max_len,
        min_confidence=min_confidence,
    ).intent
