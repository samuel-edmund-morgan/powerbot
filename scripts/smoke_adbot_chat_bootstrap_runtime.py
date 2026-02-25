#!/usr/bin/env python3
"""
Runtime smoke for adbot chat-id bootstrap in pair mode.

Checks:
- pair titles (SOURCE/INTERNAL) for multiple pairs are resolved and written to env;
- explicit non-placeholder IDs are preserved (must not be overwritten);
- legacy/e2e title-based bootstrap keys are still handled.
"""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _read_env_map(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "bootstrap_adbot_chat_ids.py"
    spec = importlib.util.spec_from_file_location("bootstrap_adbot_chat_ids", module_path)
    _assert(spec is not None and spec.loader is not None, "failed to load bootstrap module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with tempfile.TemporaryDirectory(prefix="smoke_adbot_bootstrap_") as td:
        env_path = Path(td) / ".env"
        env_path.write_text(
            "\n".join(
                [
                    "TELETHON_API_ID=33083266",
                    "TELETHON_API_HASH=87fe44a26a1a986054273f1d58ab7f3f",
                    "ADBOT_STRING_SESSION=AQB_TEST_SESSION_RUNTIME",
                    "ADBOT_SOURCE_CHAT_TITLES=Тестовий груповий чат",
                    "ADBOT_INTERNAL_CHAT_TITLE=Тестова внутрішня група",
                    "ADBOT_E2E_SOURCE_CHAT_TITLE=E2E Source",
                    "ADBOT_E2E_INTERNAL_CHAT_TITLE=E2E Internal",
                    "ADBOT_SOURCE_CHAT_IDS=your-source-chat-id",
                    "ADBOT_INTERNAL_CHAT_ID=your-internal-chat-id",
                    "ADBOT_E2E_SOURCE_CHAT_ID=your-e2e-source-chat-id",
                    "ADBOT_E2E_INTERNAL_CHAT_ID=your-e2e-internal-chat-id",
                    "ADBOT_PAIR_COUNT=3",
                    "ADBOT_PAIR_1_SOURCE_CHAT_TITLE=Pair1 Source",
                    "ADBOT_PAIR_1_INTERNAL_CHAT_TITLE=Pair1 Internal",
                    "ADBOT_PAIR_1_SOURCE_CHAT_ID=your-pair1-source",
                    "ADBOT_PAIR_1_INTERNAL_CHAT_ID=your-pair1-internal",
                    "ADBOT_PAIR_2_SOURCE_CHAT_TITLE=Pair2 Source",
                    "ADBOT_PAIR_2_INTERNAL_CHAT_TITLE=Pair2 Internal",
                    # Explicit non-placeholder ID: bootstrap must keep it unchanged.
                    "ADBOT_PAIR_2_SOURCE_CHAT_ID=-1007777777777",
                    "ADBOT_PAIR_2_INTERNAL_CHAT_ID=your-pair2-internal",
                    "ADBOT_PAIR_3_SOURCE_CHAT_TITLE=Pair3 Source",
                    "ADBOT_PAIR_3_INTERNAL_CHAT_TITLE=Pair3 Internal",
                    "ADBOT_PAIR_3_SOURCE_CHAT_ID=",
                    "ADBOT_PAIR_3_INTERNAL_CHAT_ID=",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        resolved = {
            "Тестовий груповий чат": -1001000000001,
            "Тестова внутрішня група": -1001000000002,
            "E2E Source": -1001000000003,
            "E2E Internal": -1001000000004,
            "Pair1 Source": -1001000000101,
            "Pair1 Internal": -1001000000102,
            "Pair2 Source": -1001000000201,  # should NOT override explicit pair2 source id
            "Pair2 Internal": -1001000000202,
            "Pair3 Source": -1001000000301,
            "Pair3 Internal": -1001000000302,
        }

        original_resolver = module._resolve_titles_to_ids
        try:
            async def _fake_resolver(session: str, api_id: int, api_hash: str, titles: tuple[str, ...]) -> dict[str, int]:
                _assert(session == "AQB_TEST_SESSION_RUNTIME", f"unexpected session: {session}")
                _assert(api_id == 33083266, f"unexpected api_id: {api_id}")
                _assert(api_hash == "87fe44a26a1a986054273f1d58ab7f3f", f"unexpected api_hash: {api_hash}")
                return {title: int(resolved[title]) for title in titles if title in resolved}

            module._resolve_titles_to_ids = _fake_resolver  # type: ignore[assignment]
            # Simulate CLI invocation.
            import sys

            argv_backup = list(sys.argv)
            try:
                sys.argv = ["bootstrap_adbot_chat_ids.py", "--env-file", str(env_path)]
                module.main()
            finally:
                sys.argv = argv_backup
        finally:
            module._resolve_titles_to_ids = original_resolver  # type: ignore[assignment]

        updated = _read_env_map(env_path)

        # Legacy / e2e keys updated.
        _assert(updated.get("ADBOT_SOURCE_CHAT_IDS") == "-1001000000001", "legacy source ids not bootstrapped")
        _assert(updated.get("ADBOT_INTERNAL_CHAT_ID") == "-1001000000002", "legacy internal id not bootstrapped")
        _assert(updated.get("ADBOT_E2E_SOURCE_CHAT_ID") == "-1001000000003", "e2e source id not bootstrapped")
        _assert(updated.get("ADBOT_E2E_INTERNAL_CHAT_ID") == "-1001000000004", "e2e internal id not bootstrapped")

        # Pair mode keys updated for placeholders/empty.
        _assert(updated.get("ADBOT_PAIR_1_SOURCE_CHAT_ID") == "-1001000000101", "pair1 source id not bootstrapped")
        _assert(updated.get("ADBOT_PAIR_1_INTERNAL_CHAT_ID") == "-1001000000102", "pair1 internal id not bootstrapped")
        _assert(updated.get("ADBOT_PAIR_2_INTERNAL_CHAT_ID") == "-1001000000202", "pair2 internal id not bootstrapped")
        _assert(updated.get("ADBOT_PAIR_3_SOURCE_CHAT_ID") == "-1001000000301", "pair3 source id not bootstrapped")
        _assert(updated.get("ADBOT_PAIR_3_INTERNAL_CHAT_ID") == "-1001000000302", "pair3 internal id not bootstrapped")

        # Explicit non-placeholder must stay unchanged.
        _assert(
            updated.get("ADBOT_PAIR_2_SOURCE_CHAT_ID") == "-1007777777777",
            "explicit pair2 source id was unexpectedly overwritten",
        )

    print("OK: adbot chat bootstrap runtime smoke passed.")


if __name__ == "__main__":
    main()
