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
    edit_task = asyncio.create_task(conv.get_edit(timeout=timeout_sec))
    resp_task = asyncio.create_task(conv.get_response(timeout=timeout_sec))
    done, pending = await asyncio.wait(
        {edit_task, resp_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    for task in done:
        return task.result()
    raise RuntimeError("No bot response")


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
