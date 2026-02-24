#!/usr/bin/env python3
"""
Analyze real group-chat history for adbot intent coverage and false-positives.

What this script does:
1) Reads current adbot capabilities from code (`adbot.intents`, `inline_special_queries`).
2) Loads messages from a Telegram group via Telethon for the last N months.
3) Estimates coverage:
   - how many question-like messages match current intents,
   - which intents trigger most,
   - what remains unmatched (top words + examples).

Usage example:
  # local repo run
  python3 src/tools/analyze_adbot_chat_history.py \
    --api-id "$TELETHON_API_ID" \
    --api-hash "$TELETHON_API_HASH" \
    --session "$ADBOT_STRING_SESSION" \
    --chat-title 'Ньюкасл" А-7 (ЖК "Нова Англія")' \
    --months 6 \
    --output-json /tmp/adbot_chat_analysis.json \
    --output-md /tmp/adbot_chat_analysis.md

  # inside docker test stack (remote/server)
  docker compose -f /opt/powerbot-test/docker-compose.yml exec -T adbot \
    python /app/src/tools/analyze_adbot_chat_history.py \
    --chat-title 'Ньюкасл A-7 (ЖК Нова Англія)' \
    --months 3 \
    --output-json /data/logs/adbot_chat_analysis.json \
    --output-md /data/logs/adbot_chat_analysis.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


QUESTION_PREFIXES = (
    "де ",
    "як ",
    "коли ",
    "чи ",
    "чому ",
    "хто ",
    "підкажіть",
    "скажіть",
    "дайте",
    "допоможіть",
    "потрібен номер",
    "потрібна допомога",
    "чи є ",
    "є номер",
)

STOPWORDS = {
    "і", "й", "та", "а", "але", "чи", "що", "це", "як", "де", "коли", "хто",
    "на", "в", "у", "по", "до", "за", "з", "із", "від", "для", "про", "без",
    "не", "ні", "або", "бо", "теж", "ще", "вже", "дуже", "будь", "будьласка",
    "будьласка,", "будьласка.", "будь", "ласка", "підкажіть", "скажіть",
    "дайте", "хтось", "хтоcь", "треба", "потрібно", "потрібен", "потрібна",
}


@dataclass
class Capability:
    intent_code: str
    intent_title: str
    inline_query: str
    keywords: list[str]
    strong_keywords: list[str]
    reply_title: str
    reply_description: str


def _bootstrap_imports() -> None:
    src_root = Path(__file__).resolve().parents[1]
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))


def _strip_quotes(value: str) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _parse_int(value: str | None, *, default: int = 0) -> int:
    try:
        return int(_strip_quotes(value or ""))
    except Exception:
        return default


def _load_telethon():
    try:
        from telethon import TelegramClient  # type: ignore
        from telethon.sessions import StringSession  # type: ignore
    except Exception as exc:
        raise SystemExit(
            "ERROR: Telethon is required.\n"
            "Install dependencies:\n"
            "  pip install -r requirements-dev.txt\n"
            f"Details: {exc}"
        )
    return TelegramClient, StringSession


def _normalize_text(text: str) -> str:
    value = str(text or "").lower()
    value = re.sub(r"https?://\S+", " ", value)
    value = value.replace("\n", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _is_question_like(text: str) -> bool:
    normalized = _normalize_text(text)
    if len(normalized) < 6:
        return False
    if "?" in normalized:
        return True
    for prefix in QUESTION_PREFIXES:
        if normalized.startswith(prefix):
            return True
    if any(token in normalized for token in ("підкаж", "дайте номер", "чи є", "де знайти", "як оформ")):
        return True
    return False


def _tokenize(text: str) -> list[str]:
    normalized = _normalize_text(text)
    raw_tokens = re.findall(r"[a-zA-Zа-яА-ЯіїєґІЇЄҐ0-9']+", normalized)
    tokens: list[str] = []
    for token in raw_tokens:
        token = token.strip("'")
        if len(token) < 3:
            continue
        if token in STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _build_capabilities() -> list[Capability]:
    from adbot.intents import INTENTS
    from inline_special_queries import resolve_inline_special_result

    capabilities: list[Capability] = []
    for intent in INTENTS:
        resolved = resolve_inline_special_result(intent.inline_query, cfg=None)
        reply_title = resolved.title if resolved else intent.title
        reply_description = resolved.description if resolved else ""
        capabilities.append(
            Capability(
                intent_code=intent.code,
                intent_title=intent.title,
                inline_query=intent.inline_query,
                keywords=list(intent.keywords),
                strong_keywords=list(intent.strong_keywords),
                reply_title=reply_title,
                reply_description=reply_description,
            )
        )
    return capabilities


async def _resolve_chat(client: Any, *, chat_id: int | None, chat_title: str | None) -> Any:
    if chat_id is not None:
        return await client.get_entity(chat_id)

    target = _strip_quotes(chat_title or "")
    if not target:
        raise SystemExit("ERROR: provide either --chat-id or --chat-title")

    exact: list[Any] = []
    partial: list[Any] = []
    async for dialog in client.iter_dialogs():
        title = str(getattr(dialog, "name", "") or "").strip()
        if not title:
            continue
        if title.casefold() == target.casefold():
            exact.append(dialog)
        elif target.casefold() in title.casefold():
            partial.append(dialog)

    if len(exact) == 1:
        return exact[0].entity
    if len(exact) > 1:
        options = [str(getattr(d, "name", "")).strip() for d in exact[:5]]
        raise SystemExit(f"ERROR: chat title is ambiguous (exact match): {options}")
    if len(partial) == 1:
        return partial[0].entity
    if len(partial) > 1:
        options = [str(getattr(d, "name", "")).strip() for d in partial[:8]]
        raise SystemExit(f"ERROR: chat title is ambiguous (partial match): {options}")
    raise SystemExit(f"ERROR: chat not found by title: {target}")


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _build_markdown_report(data: dict[str, Any]) -> str:
    lines: list[str] = []
    summary = data["summary"]
    lines.append("# Adbot Chat Analysis")
    lines.append("")
    lines.append(f"- Chat: `{summary['chat_title']}` (`{summary['chat_id']}`)")
    lines.append(f"- Period: `{summary['period_from']}` .. `{summary['period_to']}`")
    lines.append(f"- Scanned messages: `{summary['scanned_messages']}`")
    lines.append(f"- Text messages: `{summary['text_messages']}`")
    lines.append(f"- Question-like: `{summary['question_like_messages']}`")
    lines.append(f"- Matched: `{summary['matched_messages']}`")
    lines.append(f"- Unmatched: `{summary['unmatched_messages']}`")
    lines.append(f"- Coverage: `{summary['coverage_percent']}%`")
    lines.append("")

    lines.append("## Current Bot Capabilities")
    lines.append("")
    for capability in data["capabilities"]:
        lines.append(
            f"- `{capability['intent_code']}`: {capability['intent_title']} "
            f"(inline: `{capability['inline_query']}`)"
        )
    lines.append("")

    lines.append("## Matched Intents")
    lines.append("")
    for item in data["matched_intents"]:
        lines.append(f"- `{item['intent_code']}`: {item['count']}")
    lines.append("")

    lines.append("## Unmatched Reasons")
    lines.append("")
    for item in data["unmatched_reasons"]:
        lines.append(f"- `{item['reason']}`: {item['count']}")
    lines.append("")

    lines.append("## Top Unmatched Tokens")
    lines.append("")
    for item in data["unmatched_top_tokens"]:
        lines.append(f"- `{item['token']}`: {item['count']}")
    lines.append("")

    lines.append("## Sample Unmatched Questions")
    lines.append("")
    for sample in data["unmatched_samples"]:
        lines.append(
            f"- ({sample['reason']}) {sample['text']}"
            + (f" [best={sample['best_intent']}:{sample['best_confidence']}]" if sample["best_intent"] else "")
        )
    lines.append("")
    return "\n".join(lines) + "\n"


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    _bootstrap_imports()
    from adbot.matcher import analyze_intent_match

    TelegramClient, StringSession = _load_telethon()
    session_value = _strip_quotes(args.session)
    api_id = int(_strip_quotes(args.api_id))
    api_hash = _strip_quotes(args.api_hash)
    if not session_value:
        raise SystemExit("ERROR: missing --session (Telethon StringSession)")
    if api_id <= 0 or not api_hash:
        raise SystemExit("ERROR: missing --api-id / --api-hash")

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(int(args.months), 1) * 30)
    capabilities = _build_capabilities()

    client = TelegramClient(StringSession(session_value), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise SystemExit("ERROR: provided session is not authorized")

    entity = await _resolve_chat(
        client,
        chat_id=args.chat_id,
        chat_title=args.chat_title,
    )

    chat_title = str(getattr(entity, "title", "") or getattr(entity, "username", "") or getattr(entity, "id", ""))
    chat_id = int(getattr(entity, "id", 0) or 0)

    scanned_messages = 0
    text_messages = 0
    question_like_messages = 0
    matched_messages = 0
    unmatched_messages = 0

    matched_counter: Counter[str] = Counter()
    reason_counter: Counter[str] = Counter()
    unmatched_tokens: Counter[str] = Counter()
    unmatched_samples: list[dict[str, Any]] = []
    matched_examples: dict[str, list[str]] = defaultdict(list)

    try:
        async for msg in client.iter_messages(entity, limit=int(args.max_messages)):
            scanned_messages += 1
            message_dt = getattr(msg, "date", None)
            if isinstance(message_dt, datetime):
                message_dt = message_dt.astimezone(timezone.utc)
                if message_dt < cutoff:
                    break

            text = str(getattr(msg, "message", "") or getattr(msg, "raw_text", "") or "").strip()
            if not text:
                continue
            text_messages += 1

            if not _is_question_like(text):
                continue
            question_like_messages += 1

            diagnostics = analyze_intent_match(
                text,
                min_len=int(args.min_len),
                max_len=int(args.max_len),
                min_confidence=int(args.min_confidence),
            )
            if diagnostics.intent is not None:
                matched_messages += 1
                matched_counter[diagnostics.intent.code] += 1
                samples = matched_examples[diagnostics.intent.code]
                if len(samples) < int(args.sample_matched_per_intent):
                    samples.append(text)
            else:
                unmatched_messages += 1
                reason_counter[diagnostics.reason] += 1
                for token in _tokenize(text):
                    unmatched_tokens[token] += 1
                if len(unmatched_samples) < int(args.sample_unmatched):
                    unmatched_samples.append(
                        {
                            "text": text,
                            "reason": diagnostics.reason,
                            "best_intent": diagnostics.best_intent,
                            "best_confidence": diagnostics.best_confidence,
                            "best_signals": diagnostics.best_signals,
                        }
                    )
    finally:
        await client.disconnect()

    coverage_percent = round((matched_messages / question_like_messages * 100), 2) if question_like_messages else 0.0
    period_to = datetime.now(timezone.utc)

    matched_intents = [
        {"intent_code": intent_code, "count": count}
        for intent_code, count in matched_counter.most_common()
    ]
    unmatched_reasons = [
        {"reason": reason, "count": count}
        for reason, count in reason_counter.most_common()
    ]
    unmatched_top_tokens = [
        {"token": token, "count": count}
        for token, count in unmatched_tokens.most_common(int(args.top_tokens))
    ]

    data: dict[str, Any] = {
        "summary": {
            "chat_title": chat_title,
            "chat_id": chat_id,
            "period_from": _to_iso(cutoff),
            "period_to": _to_iso(period_to),
            "scanned_messages": scanned_messages,
            "text_messages": text_messages,
            "question_like_messages": question_like_messages,
            "matched_messages": matched_messages,
            "unmatched_messages": unmatched_messages,
            "coverage_percent": coverage_percent,
            "matcher_config": {
                "min_len": int(args.min_len),
                "max_len": int(args.max_len),
                "min_confidence": int(args.min_confidence),
            },
        },
        "capabilities": [asdict(capability) for capability in capabilities],
        "matched_intents": matched_intents,
        "matched_examples": matched_examples,
        "unmatched_reasons": unmatched_reasons,
        "unmatched_top_tokens": unmatched_top_tokens,
        "unmatched_samples": unmatched_samples,
    }
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-id", default=os.getenv("TELETHON_API_ID", ""))
    parser.add_argument("--api-hash", default=os.getenv("TELETHON_API_HASH", ""))
    parser.add_argument(
        "--session",
        default=(
            os.getenv("ADBOT_STRING_SESSION", "")
            or os.getenv("ADBOT_E2E_DRIVER_STRING_SESSION", "")
            or os.getenv("TESTERBOT_STRING_SESSION", "")
        ),
    )
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument("--chat-title", default="")
    parser.add_argument("--months", type=int, default=3)
    parser.add_argument("--max-messages", type=int, default=50000)
    parser.add_argument("--min-len", type=int, default=10)
    parser.add_argument("--max-len", type=int, default=280)
    parser.add_argument("--min-confidence", type=int, default=120)
    parser.add_argument("--sample-unmatched", type=int, default=40)
    parser.add_argument("--sample-matched-per-intent", type=int, default=5)
    parser.add_argument("--top-tokens", type=int, default=40)
    parser.add_argument("--output-json", default="adbot_chat_analysis.json")
    parser.add_argument("--output-md", default="")
    args = parser.parse_args()

    report = asyncio.run(_run(args))

    out_json = Path(args.output_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: wrote JSON report to {out_json}")

    output_md = _strip_quotes(args.output_md)
    if output_md:
        md_path = Path(output_md)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(_build_markdown_report(report), encoding="utf-8")
        print(f"OK: wrote Markdown report to {md_path}")
    else:
        print(
            "Summary:",
            f"coverage={report['summary']['coverage_percent']}%",
            f"matched={report['summary']['matched_messages']}",
            f"unmatched={report['summary']['unmatched_messages']}",
        )


if __name__ == "__main__":
    main()
