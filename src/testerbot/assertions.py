"""Shared assertions/helpers for testerbot scenarios."""

from __future__ import annotations

from typing import Iterable


def assert_contains(text: str, tokens: Iterable[str], *, ctx: str) -> None:
    """Assert that all expected text tokens are present."""
    for token in tokens:
        if token not in text:
            raise AssertionError(
                f"{ctx}: expected `{token}` in:\n{text}"
            )


def assert_contains_any(text: str, tokens: Iterable[str], *, ctx: str) -> None:
    """Assert that at least one expected token is present."""
    token_list = list(tokens)
    for token in token_list:
        if token in text:
            return
    raise AssertionError(
        f"{ctx}: expected one of {token_list} in:\n{text}"
    )


def first_message_text(message) -> str:
    """Normalize any ai-like message object to plain text."""
    return str(getattr(message, "text", "") or getattr(message, "raw_text", "") or "").strip()
