"""Common utilities for testerbot scenarios."""

from __future__ import annotations

from collections.abc import Callable

import asyncio
import logging
from datetime import datetime
from typing import Any

from testerbot.assertions import first_message_text

logger = logging.getLogger(__name__)


async def wait_for_bot_response(conv, timeout_sec: int):
    # Conversation futures are sensitive to concurrent cancellation races.
    # Poll edit/response sequentially in short windows until timeout budget.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(timeout_sec, 1)
    last_error: Exception | None = None
    while loop.time() < deadline:
        remaining = max(0.1, deadline - loop.time())
        window = min(2.0, remaining)
        try:
            return await conv.get_edit(timeout=window)
        except Exception as exc:  # pragma: no cover - runtime-dependent
            last_error = exc
        remaining = max(0.1, deadline - loop.time())
        window = min(2.0, remaining)
        try:
            return await conv.get_response(timeout=window)
        except Exception as exc:  # pragma: no cover - runtime-dependent
            last_error = exc
    if last_error is not None:
        raise TimeoutError("No bot response within timeout") from last_error
    raise TimeoutError("No bot response within timeout")


def find_button(message, needle: str) -> tuple[int, int]:
    """Find button coordinates containing `needle`."""
    buttons = getattr(message, "buttons", None) or []
    needle_norm = needle.casefold()
    for row_idx, row in enumerate(buttons):
        for btn_idx, btn in enumerate(row):
            text = str(getattr(btn, "text", "")).strip()
            if needle_norm in text.casefold():
                return row_idx, btn_idx
    available = []
    for row in buttons:
        for btn in row:
            available.append(str(getattr(btn, "text", "")).strip())
    raise AssertionError(f"button containing `{needle}` not found. available={available}")


async def click_button_and_wait(conv, message, needle: str, timeout_sec: int):
    i, j = find_button(message, needle)
    await message.click(i, j)
    return await wait_for_bot_response(conv, timeout_sec)


async def run_callback_safe(callback: Callable[[], Any], timeout_sec: int) -> Any:
    started = datetime.now().timestamp()
    try:
        return await callback()
    finally:
        if (datetime.now().timestamp() - started) > timeout_sec:
            logger.warning("callback exceeded timeout budget (%ss)", timeout_sec)


def extract_text(message) -> str:
    return first_message_text(message)
