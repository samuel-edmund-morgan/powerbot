#!/usr/bin/env python3
"""
Static smoke-check: resident Verified badge policy.

Policy:
- Catalog list shows tier marker for verified places (⭐/🔝/✅) in business branch.
- Place detail card renders `✅ Verified...` (or partner title) only under business+verified guard.
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
    text = _read(root / "src" / "handlers.py")
    errors: list[str] = []

    # Catalog marker policy for verified entries.
    _must(text, 'if business_enabled and has_verified and place.get("is_verified"):', errors=errors)
    _must(text, 'verified_prefix = "⭐"', errors=errors)
    _must(text, 'verified_prefix = "🔝"', errors=errors)
    _must(text, 'verified_prefix = "✅"', errors=errors)
    _must(text, 'prefix_parts = [p for p in [medal_prefix, verified_prefix] if p]', errors=errors)

    # Detail card badge policy.
    _must(text, 'if is_business_feature_enabled() and place_enriched.get("is_verified"):', errors=errors)
    _must(text, 'text += "⭐ <b>Офіційний партнер категорії</b>\\n\\n"', errors=errors)
    _must(text, 'text += f"✅ <b>Verified{tier_text}</b>\\n\\n"', errors=errors)
    _must(text, 'tier_norm = str(place_enriched.get("verified_tier") or "").strip().lower()', errors=errors)

    if errors:
        raise SystemExit("ERROR: business verified badge policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business verified badge policy smoke passed.")


if __name__ == "__main__":
    main()
