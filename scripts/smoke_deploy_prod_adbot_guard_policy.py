#!/usr/bin/env python3
"""
Static smoke-check: prod adbot config guard in deploy script.

Policy:
- deploy_prod must validate adbot prod env before enabling adbot profile.
- guard must reject test mode and placeholder/missing critical vars.
"""

from __future__ import annotations

from pathlib import Path


def _resolve(path_rel: str) -> Path:
    candidates: list[Path] = []
    try:
        candidates.append(Path(__file__).resolve().parents[1] / path_rel)
    except Exception:
        pass
    candidates.extend([Path.cwd() / path_rel, Path("/app") / path_rel])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else Path(path_rel)


DEPLOY_PROD = _resolve("scripts/deploy_prod.sh")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _must(text: str, token: str, *, errors: list[str]) -> None:
    if token not in text:
        errors.append(f"scripts/deploy_prod.sh: missing token `{token}`")


def main() -> None:
    _assert(DEPLOY_PROD.exists(), f"file not found: {DEPLOY_PROD}")
    text = DEPLOY_PROD.read_text(encoding="utf-8")
    errors: list[str] = []

    for token in (
        "is_placeholder_value()",
        "count_numeric_chat_ids()",
        "ensure_adbot_prod_config()",
        "ADBOT_TEST_MODE must be 0 in prod when ADBOT_ENABLED=1.",
        "TELETHON_API_ID must be a numeric value when ADBOT_ENABLED=1.",
        "TELETHON_API_HASH is empty or placeholder when ADBOT_ENABLED=1.",
        "ADBOT_STRING_SESSION is empty or placeholder when ADBOT_ENABLED=1.",
        "ADBOT_TARGET_POWERBOT_USERNAME is empty or placeholder when ADBOT_ENABLED=1.",
        "ADBOT_SOURCE_CHAT_IDS must contain at least one numeric chat id when ADBOT_ENABLED=1.",
        "ADBOT_INTERNAL_CHAT_ID must be a numeric chat id when ADBOT_ENABLED=1.",
        "ensure_adbot_prod_config \"${PROD_DIR}/.env\"",
    ):
        _must(text, token, errors=errors)

    if errors:
        raise SystemExit(
            "ERROR: deploy_prod adbot guard policy violation(s):\n"
            + "\n".join(f"- {err}" for err in errors)
        )

    print("OK: deploy_prod adbot guard policy smoke passed.")


if __name__ == "__main__":
    main()
