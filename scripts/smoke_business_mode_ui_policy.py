#!/usr/bin/env python3
"""
Static smoke-check: resident places UI BUSINESS_MODE policy in handlers.

Policy goals:
- In category list flow, business mode applies business ranking only when
  the category already contains at least one Verified place (to keep "stealth"
  BUSINESS_MODE enablement UX-neutral when there are no verified places yet).
- Business ranking contract:
  partner block -> promo slot (single top pro) -> verified by likes -> unverified.
- Legacy branch keeps likes-first medal behavior when business mode is off.
- Place details add Verified badge only under business feature guard.

Run:
  python3 scripts/smoke_business_mode_ui_policy.py
"""

from __future__ import annotations

from pathlib import Path


def _resolve(path_rel: str) -> Path:
    candidates = []
    try:
        candidates.append(Path(__file__).resolve().parents[1] / path_rel)
    except Exception:
        pass
    candidates.extend([Path.cwd() / path_rel, Path("/app") / path_rel])
    for p in candidates:
        if p.exists():
            return p
    return candidates[0] if candidates else Path(path_rel)


HANDLERS_FILE = _resolve("src/handlers.py")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> None:
    _assert(HANDLERS_FILE.exists(), f"file not found: {HANDLERS_FILE}")
    text = HANDLERS_FILE.read_text(encoding="utf-8")

    violations: list[str] = []

    # Category list: business branch + ranking contract.
    if "business_enabled = is_business_feature_enabled()" not in text:
        violations.append("cb_places_category must derive business_enabled via feature guard")
    if "has_verified" not in text or "item.get(\"is_verified\")" not in text:
        violations.append("cb_places_category must gate business ranking by presence of Verified places")
    if "if business_enabled and has_verified" not in text:
        violations.append("cb_places_category must apply business ranking only when has_verified")
    if (
        "partner block -> promo slot (single top PRO) -> verified by likes -> unverified." not in text
        and "partner slot (single top Partner) -> promo slot (single top PRO) -> verified by likes -> unverified." not in text
    ):
        violations.append("business branch must document partner->promo->verified->unverified contract")
    if "promo_slot = pro_places[0] if pro_places else None" not in text:
        violations.append("business branch must select single promo_slot from pro places")
    if "verified_by_likes.sort(" not in text:
        violations.append("business branch must sort remaining verified places by likes")
    if "places.extend(unverified_places)" not in text:
        violations.append("business branch must append unverified places after verified blocks")

    # Category list: legacy branch (mode off) keeps likes-first medals.
    if "top_by_likes = sorted(places, key=lambda item: -(item.get(\"likes_count\") or 0))[:3]" not in text:
        violations.append("legacy branch must keep likes-first medal mapping")

    # Place card: Verified badge only under feature guard.
    if "if is_business_feature_enabled() and place_enriched.get(\"is_verified\"):" not in text:
        violations.append("cb_place_detail must guard Verified badge by business feature flag")
    if "text += f\"✅ <b>Verified" not in text:
        violations.append("cb_place_detail must include Verified badge rendering")

    if violations:
        raise SystemExit(
            "ERROR: business mode UI policy violation(s):\n"
            + "\n".join(f"- {v}" for v in violations)
        )

    print("OK: business mode UI policy smoke passed.")


if __name__ == "__main__":
    main()
