#!/usr/bin/env python3
"""
Static smoke-check for resident catalog ranking/markers policy.

Policy:
- business branch builds explicit order:
  partner block -> promo slot (single top PRO) -> verified-by-likes -> unverified.
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
    _must(text, "verified_places = [item for item in places if item.get(\"is_verified\")]", errors=errors)
    _must(text, "unverified_places = [item for item in places if not item.get(\"is_verified\")]", errors=errors)
    _must(text, "partner_places =", errors=errors)
    _must(text, "pro_places =", errors=errors)
    _must(text, "promo_slot = pro_places[0] if pro_places else None", errors=errors)
    _must(text, "verified_by_likes.sort(", errors=errors)
    _must(text, "places.extend(unverified_places)", errors=errors)

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
