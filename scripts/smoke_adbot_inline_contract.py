#!/usr/bin/env python3
"""
Static/runtime smoke for adbot inline contract.

Validates:
- Every adbot intent has a stable inline query in contract.
- Every inline query resolves to a special response block in resident bot.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _bootstrap_imports() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))


def main() -> None:
    _bootstrap_imports()

    from adbot.intents import INTENTS
    from inline_special_queries import ADBOT_INLINE_QUERY_CONTRACT, resolve_inline_special_result

    cfg = SimpleNamespace(
        electrician_phone="067-576-22-42",
        plumber_phone="067-000-00-01",
        security_phone="067-000-00-02",
    )

    contract_map = dict(ADBOT_INLINE_QUERY_CONTRACT)
    intent_codes = {intent.code for intent in INTENTS}
    contract_codes = set(contract_map.keys())
    _assert(intent_codes == contract_codes, f"intent/contract mismatch: {intent_codes ^ contract_codes}")

    for intent in INTENTS:
        expected_query = contract_map[intent.code]
        _assert(
            intent.inline_query == expected_query,
            f"inline_query mismatch for {intent.code}: {intent.inline_query!r} != {expected_query!r}",
        )
        resolved = resolve_inline_special_result(expected_query, cfg=cfg)
        _assert(resolved is not None, f"query does not resolve: {expected_query!r}")
        _assert(bool(resolved.title.strip()), f"empty title for {intent.code}")
        _assert(bool(resolved.description.strip()), f"empty description for {intent.code}")
        _assert(bool(resolved.message_text.strip()), f"empty message text for {intent.code}")

    print("OK: adbot inline contract smoke passed.")


if __name__ == "__main__":
    main()

