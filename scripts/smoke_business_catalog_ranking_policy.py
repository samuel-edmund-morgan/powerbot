#!/usr/bin/env python3
"""
Static smoke-check for resident catalog ranking/markers policy.

Policy:
- business branch sorts by: verified -> tier(partner/pro/light) -> likes -> name
- UI uses tier markers: ⭐ partner, 🔝 promo(pro), ✅ verified(light+)
"""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: file not found: {path}")
    return path.read_text(encoding="utf-8")


def _must(text: str, token: str, *, errors: list[str]) -> None:
    if token not in text:
        errors.append(f"missing token: {token}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    text = _read(root / "src/handlers.py")
    errors: list[str] = []

    # Ranking contract.
    _must(text, 'if business_enabled and has_verified:', errors=errors)
    _must(text, 'places.sort(', errors=errors)
    _must(text, '0 if item.get("is_verified") else 1,', errors=errors)
    _must(text, '_tier_rank(item.get("verified_tier"))', errors=errors)
    _must(text, '-(item.get("likes_count") or 0),', errors=errors)
    _must(text, 'return {"partner": 0, "pro": 1, "light": 2}.get(tier, 3)', errors=errors)

    # Marker contract.
    _must(text, 'verified_prefix = "⭐"', errors=errors)
    _must(text, 'verified_prefix = "🔝"', errors=errors)
    _must(text, 'verified_prefix = "✅"', errors=errors)
    _must(text, 'ranking_hint = "⭐ партнер • 🔝 промо • ✅ verified', errors=errors)

    if errors:
        raise SystemExit("ERROR: business catalog ranking policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business catalog ranking policy smoke passed.")


if __name__ == "__main__":
    main()

