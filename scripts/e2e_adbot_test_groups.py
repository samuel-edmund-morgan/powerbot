#!/usr/bin/env python3
"""
Optional real Telegram E2E runner for adbot group-chat flow.

Scenarios (test groups):
1) "дайте номер електрика"
2) "чи є світло в Ньюкасл"
3) "де оформити перепустку в паркінг"

The script sends messages to source chat and verifies:
- adbot replies in source chat as reply to original message,
- response text matches expected intent family,
- internal audit chat gets adbot summary, and (optionally) forwarded original message.

Required env:
  TELETHON_API_ID
  TELETHON_API_HASH
  ADBOT_E2E_DRIVER_STRING_SESSION
  ADBOT_E2E_SOURCE_CHAT_ID

Optional env:
  ADBOT_E2E_INTERNAL_CHAT_ID
  ADBOT_E2E_TIMEOUT_SEC      (default: 45)
  ADBOT_E2E_POLL_SEC         (default: 1.0)
  ADBOT_E2E_NEGATIVE_WAIT_SEC (default: 12)
  ADBOT_E2E_VERIFY_FORWARD   (default: 1)
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Iterable


def _require_env(name: str) -> str:
    value = str(os.getenv(name, "")).strip()
    if not value:
        raise SystemExit(f"ERROR: missing required env `{name}`")
    return value


def _parse_bool(raw: str, default: bool = False) -> bool:
    value = str(raw or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _load_telethon():
    try:
        from telethon import TelegramClient  # type: ignore
        from telethon.sessions import StringSession  # type: ignore
    except Exception as exc:
        raise SystemExit(
            "ERROR: telethon is required for this script.\n"
            "Install dev dependencies:\n"
            "  pip install -r requirements-dev.txt\n"
            f"Details: {exc}"
        )
    return TelegramClient, StringSession


@dataclass(frozen=True)
class Scenario:
    code: str
    prompt: str
    expected_reply_tokens: tuple[str, ...]
    expected_intent_tokens: tuple[str, ...]


def _reply_to_msg_id(message) -> int | None:
    direct = getattr(message, "reply_to_msg_id", None)
    if isinstance(direct, int):
        return direct
    reply_obj = getattr(message, "reply_to", None)
    if reply_obj is not None:
        nested = getattr(reply_obj, "reply_to_msg_id", None)
        if isinstance(nested, int):
            return nested
    return None


def _contains_any(text: str, tokens: Iterable[str]) -> bool:
    normalized = (text or "").casefold()
    for token in tokens:
        if token.casefold() in normalized:
            return True
    return False


async def _wait_for_source_reply(
    client,
    *,
    source_chat_id: int,
    original_message_id: int,
    expected_tokens: tuple[str, ...],
    timeout_sec: int,
    poll_sec: float,
):
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        messages = await client.get_messages(source_chat_id, limit=40)
        for message in messages:
            reply_to = _reply_to_msg_id(message)
            if reply_to != original_message_id:
                continue
            text = str(getattr(message, "raw_text", "") or getattr(message, "text", "") or "")
            if _contains_any(text, expected_tokens):
                return message
        await asyncio.sleep(poll_sec)
    raise AssertionError(
        f"timeout waiting source reply for msg_id={original_message_id} tokens={expected_tokens}"
    )


async def _assert_no_source_reply(
    client,
    *,
    source_chat_id: int,
    original_message_id: int,
    wait_sec: int,
    poll_sec: float,
):
    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        messages = await client.get_messages(source_chat_id, limit=40)
        for message in messages:
            reply_to = _reply_to_msg_id(message)
            if reply_to == original_message_id:
                text = str(getattr(message, "raw_text", "") or getattr(message, "text", "") or "")
                raise AssertionError(
                    f"unexpected adbot reply in anti-false-positive case for msg_id={original_message_id}: {text[:180]}"
                )
        await asyncio.sleep(poll_sec)


async def _wait_for_internal_audit(
    client,
    *,
    internal_chat_id: int,
    baseline_id: int,
    prompt_with_nonce: str,
    expected_intent_tokens: tuple[str, ...],
    verify_forward: bool,
    timeout_sec: int,
    poll_sec: float,
):
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        messages = await client.get_messages(internal_chat_id, limit=80)
        new_messages = [m for m in messages if int(getattr(m, "id", 0) or 0) > baseline_id]
        if not new_messages:
            await asyncio.sleep(poll_sec)
            continue

        summary_ok = False
        forward_ok = not verify_forward
        for message in new_messages:
            text = str(getattr(message, "raw_text", "") or getattr(message, "text", "") or "")
            if "adbot match" in text.casefold():
                if _contains_any(text, expected_intent_tokens):
                    summary_ok = True
            else:
                if prompt_with_nonce.casefold() in text.casefold():
                    forward_ok = True
                elif getattr(message, "fwd_from", None) is not None:
                    # Some clients can trim text previews for forwarded posts.
                    forward_ok = True

        if summary_ok and forward_ok:
            return

        await asyncio.sleep(poll_sec)

    raise AssertionError(
        f"timeout waiting internal audit (summary/forward). intents={expected_intent_tokens} "
        f"verify_forward={verify_forward}"
    )


async def _assert_no_internal_audit_for_prompt(
    client,
    *,
    internal_chat_id: int,
    baseline_id: int,
    prompt_with_nonce: str,
    wait_sec: int,
    poll_sec: float,
):
    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        messages = await client.get_messages(internal_chat_id, limit=80)
        new_messages = [m for m in messages if int(getattr(m, "id", 0) or 0) > baseline_id]
        for message in new_messages:
            text = str(getattr(message, "raw_text", "") or getattr(message, "text", "") or "")
            if prompt_with_nonce.casefold() in text.casefold():
                raise AssertionError(
                    "unexpected internal audit/forward for anti-false-positive case"
                )
        await asyncio.sleep(poll_sec)


async def _run() -> None:
    TelegramClient, StringSession = _load_telethon()
    api_id = int(_require_env("TELETHON_API_ID"))
    api_hash = _require_env("TELETHON_API_HASH")
    source_chat_id = int(_require_env("ADBOT_E2E_SOURCE_CHAT_ID"))
    internal_chat_raw = str(os.getenv("ADBOT_E2E_INTERNAL_CHAT_ID", "")).strip()
    internal_chat_id = int(internal_chat_raw) if internal_chat_raw else None
    timeout_sec = int(str(os.getenv("ADBOT_E2E_TIMEOUT_SEC", "45")).strip())
    poll_sec = float(str(os.getenv("ADBOT_E2E_POLL_SEC", "1.0")).strip())
    negative_wait_sec = int(str(os.getenv("ADBOT_E2E_NEGATIVE_WAIT_SEC", "12")).strip())
    verify_forward = _parse_bool(os.getenv("ADBOT_E2E_VERIFY_FORWARD", "1"), default=True)

    session = _require_env("ADBOT_E2E_DRIVER_STRING_SESSION")
    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise SystemExit("ERROR: ADBOT_E2E_DRIVER_STRING_SESSION is not authorized.")

    scenarios = (
        Scenario(
            code="electrician",
            prompt="Дайте номер електрика, будь ласка",
            expected_reply_tokens=("Електрик", "067", "електрик"),
            expected_intent_tokens=("electrician",),
        ),
        Scenario(
            code="light_status",
            prompt="Чи є світло в Ньюкасл?",
            expected_reply_tokens=("Статус світла", "світла", "електропостачання"),
            expected_intent_tokens=("light_status",),
        ),
        Scenario(
            code="parking_or_car_pass",
            prompt="Де оформити перепустку в паркінг?",
            expected_reply_tokens=("Перепустка", "Паркінг", "перепуст", "паркінг"),
            expected_intent_tokens=("car_pass", "parking"),
        ),
    )

    try:
        for idx, scenario in enumerate(scenarios, start=1):
            nonce = f"e2e-{int(time.time())}-{idx}"
            prompt = f"{scenario.prompt} ({nonce})"

            internal_baseline = 0
            if internal_chat_id is not None:
                latest_internal = await client.get_messages(internal_chat_id, limit=1)
                if latest_internal:
                    internal_baseline = int(getattr(latest_internal[0], "id", 0) or 0)

            source_message = await client.send_message(source_chat_id, prompt)
            source_message_id = int(getattr(source_message, "id", 0) or 0)
            if source_message_id <= 0:
                raise AssertionError(f"failed to send source message for scenario {scenario.code}")

            reply = await _wait_for_source_reply(
                client,
                source_chat_id=source_chat_id,
                original_message_id=source_message_id,
                expected_tokens=scenario.expected_reply_tokens,
                timeout_sec=timeout_sec,
                poll_sec=poll_sec,
            )
            reply_text = str(getattr(reply, "raw_text", "") or getattr(reply, "text", "") or "")
            print(f"OK source reply [{scenario.code}]: {reply_text[:120]}")

            if internal_chat_id is not None:
                await _wait_for_internal_audit(
                    client,
                    internal_chat_id=internal_chat_id,
                    baseline_id=internal_baseline,
                    prompt_with_nonce=prompt,
                    expected_intent_tokens=scenario.expected_intent_tokens,
                    verify_forward=verify_forward,
                    timeout_sec=timeout_sec,
                    poll_sec=poll_sec,
                )
                print(f"OK internal audit [{scenario.code}]")

        # Anti-false-positive: long noisy text with one weak signal should not trigger adbot.
        negative_nonce = f"e2e-negative-{int(time.time())}"
        negative_prompt = (
            "Сьогодні обговорюємо ремонт підʼїзду, доставку матеріалів та графік робіт, "
            "нічого не питаємо про контакти служб, просто довге повідомлення зі словом світло "
            f"для перевірки анти-фолс-позитиву ({negative_nonce})"
        )
        internal_baseline = 0
        if internal_chat_id is not None:
            latest_internal = await client.get_messages(internal_chat_id, limit=1)
            if latest_internal:
                internal_baseline = int(getattr(latest_internal[0], "id", 0) or 0)

        negative_source = await client.send_message(source_chat_id, negative_prompt)
        negative_source_id = int(getattr(negative_source, "id", 0) or 0)
        if negative_source_id <= 0:
            raise AssertionError("failed to send anti-false-positive source message")

        await _assert_no_source_reply(
            client,
            source_chat_id=source_chat_id,
            original_message_id=negative_source_id,
            wait_sec=negative_wait_sec,
            poll_sec=poll_sec,
        )
        print("OK anti-false-positive: no source reply")

        if internal_chat_id is not None:
            await _assert_no_internal_audit_for_prompt(
                client,
                internal_chat_id=internal_chat_id,
                baseline_id=internal_baseline,
                prompt_with_nonce=negative_prompt,
                wait_sec=negative_wait_sec,
                poll_sec=poll_sec,
            )
            print("OK anti-false-positive: no internal audit")

        print("OK: adbot E2E test-groups suite passed.")
    finally:
        await client.disconnect()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
