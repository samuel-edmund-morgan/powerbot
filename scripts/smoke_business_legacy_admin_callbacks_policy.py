#!/usr/bin/env python3
"""
Static smoke-check: businessbot legacy admin callback policy.

Policy:
- Legacy admin callbacks in businessbot must be handled only by
  `cb_legacy_admin_feature_moved`.
- No dedicated `@router.callback_query(...)` handlers for old moderation/token
  callbacks should remain in `src/business/handlers.py`.

Run:
  python3 scripts/smoke_business_legacy_admin_callbacks_policy.py
"""

from __future__ import annotations

from pathlib import Path
import re


ALLOWED_LEGACY_HANDLER = "cb_legacy_admin_feature_moved"

REQUIRED_ALLOWED_TOKENS = (
    "F.data == CB_MENU_MOD",
    "F.data == CB_MENU_TOKENS",
    'F.data.startswith("bmod_")',
    'F.data.startswith("bm:")',
    'F.data.startswith("btok")',
)

LEGACY_DECORATOR_MARKERS = (
    "CB_MENU_MOD",
    "CB_MENU_TOKENS",
    "CB_TOK",
    "CB_MOD_",
    '"bm:"',
    "'bm:'",
    '"btok"',
    "'btok'",
    '"bmod_"',
    "'bmod_'",
)


def _resolve_handlers_path() -> Path:
    candidates: list[Path] = []
    try:
        candidates.append(Path(__file__).resolve().parents[1] / "src/business/handlers.py")
    except Exception:
        pass
    candidates.extend(
        [
            Path.cwd() / "src/business/handlers.py",
            Path("/app/src/business/handlers.py"),
        ]
    )
    for path in candidates:
        if path.exists():
            return path
    return candidates[0] if candidates else Path("src/business/handlers.py")


def _extract_callback_decorators(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    results: list[tuple[str, str]] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx].lstrip()
        if not line.startswith("@router.callback_query("):
            idx += 1
            continue

        start = idx
        idx += 1
        while idx < len(lines) and not lines[idx].lstrip().startswith("async def "):
            idx += 1
        if idx >= len(lines):
            break

        func_line = lines[idx].strip()
        match = re.match(r"async\s+def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", func_line)
        if not match:
            idx += 1
            continue

        func_name = match.group(1)
        decorator_block = "\n".join(lines[start:idx])
        results.append((func_name, decorator_block))
        idx += 1

    return results


def main() -> None:
    handlers_path = _resolve_handlers_path()
    if not handlers_path.exists():
        raise SystemExit(f"ERROR: file not found: {handlers_path}")

    text = handlers_path.read_text(encoding="utf-8")
    callback_decorators = _extract_callback_decorators(text)
    violations: list[str] = []

    allowed_count = 0
    for func_name, decorator_block in callback_decorators:
        is_legacy_callback = any(marker in decorator_block for marker in LEGACY_DECORATOR_MARKERS)
        if not is_legacy_callback:
            continue

        if func_name != ALLOWED_LEGACY_HANDLER:
            violations.append(
                f"legacy callback marker found in disallowed handler `{func_name}`"
            )
            continue

        allowed_count += 1
        for token in REQUIRED_ALLOWED_TOKENS:
            if token not in decorator_block:
                violations.append(
                    f"{ALLOWED_LEGACY_HANDLER} missing required legacy route token: {token}"
                )

    if allowed_count != 1:
        violations.append(
            f"expected exactly 1 allowed legacy callback handler, found {allowed_count}"
        )

    if violations:
        message = "\n".join(f"- {item}" for item in violations)
        raise SystemExit(
            "ERROR: business legacy admin callbacks policy violation(s):\n" + message
        )

    print("OK: business legacy admin callbacks policy smoke passed.")


if __name__ == "__main__":
    main()
