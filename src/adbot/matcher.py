"""Rule-based matcher for group messages."""

from __future__ import annotations

from dataclasses import dataclass
import re

from adbot.intents import INTENTS, Intent


def normalize(text: str) -> str:
    return (text or "").strip().lower()


def _match_signals(text_norm: str, intent: Intent) -> tuple[int, int]:
    score = 0
    for token in intent.keywords:
        # partial substring match allows flexible Ukrainian variants.
        if token in text_norm:
            score += 1
    strong_hits = 0
    for token in intent.strong_keywords:
        if token in text_norm:
            strong_hits += 1
    return score, strong_hits


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


def _is_question_like(text_norm: str) -> bool:
    value = str(text_norm or "").strip()
    if not value:
        return False
    if "?" in value:
        return True
    question_prefixes = (
        "де ",
        "як ",
        "коли ",
        "чи ",
        "чому ",
        "хто ",
        "підкажіть",
        "скажіть",
        "дайте",
        "потрібен номер",
        "є номер",
        "контакти ",
    )
    return any(value.startswith(prefix) for prefix in question_prefixes)


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
    best_missing_strong: tuple[int, int, Intent] | None = None
    for intent in INTENTS:
        score, strong_hits = _match_signals(norm, intent)
        if score < intent.required_signals:
            continue
        confidence = _confidence(score, token_count)
        if intent.strong_keywords and strong_hits <= 0:
            if best_missing_strong is None or confidence > best_missing_strong[0]:
                best_missing_strong = (confidence, score, intent)
            continue
        if best_candidate is None or confidence > best_candidate[0]:
            best_candidate = (confidence, score, intent)

    if best_candidate is None:
        if best_missing_strong is not None:
            missing_confidence, missing_signals, missing_intent = best_missing_strong
            return MatchDiagnostics(
                intent=None,
                reason="missing_strong_signal",
                text_len=text_len,
                token_count=token_count,
                best_intent=missing_intent.code,
                best_confidence=missing_confidence,
                best_signals=missing_signals,
            )
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

    # Additional anti-false-positive guard:
    # long non-question discussions with incidental keywords must not trigger adbot.
    if text_len >= 80 and not _is_question_like(norm):
        # Keep truly high-signal short-intent requests eligible,
        # but block long narrative paragraphs unless confidence is very high.
        if best_confidence < max(int(min_confidence) + 120, 260):
            return MatchDiagnostics(
                intent=None,
                reason="non_question_long_text",
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
