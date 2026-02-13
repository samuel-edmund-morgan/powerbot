#!/usr/bin/env python3
"""
Static smoke-check: BUSINESS_MODE guard policy in resident-facing code.

Policy intent:
- verified/business visual behavior in resident bot must be gated by
  `is_business_feature_enabled()` (or derived guarded boolean).
- API verified-first sorting/search path must be enabled only under the same guard.

This is a focused regression test for known integration points.

Run:
  python3 scripts/smoke_business_guard_policy.py
"""

from __future__ import annotations

from pathlib import Path


def _resolve(path_rel: str) -> Path:
    candidates = []
    try:
        candidates.append(Path(__file__).resolve().parents[1] / path_rel)
    except Exception:
        pass
    candidates.extend(
        [
            Path.cwd() / path_rel,
            Path("/app") / path_rel,
        ]
    )
    for p in candidates:
        if p.exists():
            return p
    return candidates[0] if candidates else Path(path_rel)


HANDLERS_FILE = _resolve("src/handlers.py")
API_FILE = _resolve("src/api_server.py")


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _read(path: Path) -> str:
    _assert(path.exists(), f"file not found: {path}")
    return path.read_text(encoding="utf-8")


def main() -> None:
    handlers_text = _read(HANDLERS_FILE)
    api_text = _read(API_FILE)

    required_handler_snippets = [
        "business_enabled = is_business_feature_enabled()",
        'verified_prefix = "✅" if (business_enabled and place.get("is_verified")) else None',
        "if is_business_feature_enabled() and place_enriched.get(\"is_verified\"):",
    ]
    required_api_snippets = [
        "places = _filter_places_by_query(base_places, query, verified_first=is_business_feature_enabled())",
        "if is_business_feature_enabled():",
        "0 if item.get(\"is_verified\") else 1,",
    ]

    missing: list[str] = []
    for snippet in required_handler_snippets:
        if snippet not in handlers_text:
            missing.append(f"{HANDLERS_FILE}: missing guard snippet -> {snippet}")

    for snippet in required_api_snippets:
        if snippet not in api_text:
            missing.append(f"{API_FILE}: missing guard snippet -> {snippet}")

    if missing:
        raise SystemExit(
            "ERROR: business guard policy violation(s) detected:\n" + "\n".join(f"- {item}" for item in missing)
        )

    print("OK: business guard policy smoke passed.")


if __name__ == "__main__":
    main()
