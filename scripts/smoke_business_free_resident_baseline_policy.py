#!/usr/bin/env python3
"""
Static smoke-check: Free-tier resident baseline contract.

Policy:
- Catalog is accessible via categories (`places_menu` -> `places_cat_*`).
- Resident list/details expose likes for all places.
- Free place card remains minimal (paid CTAs are gated elsewhere);
  baseline controls here verify stable core buttons/copy.
- Location line + map rendering path stays present.
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

    # Catalog by categories.
    _must(text, '@router.callback_query(F.data == "places_menu")', errors=errors)
    _must(text, '@router.callback_query(F.data.startswith("places_cat_"))', errors=errors)
    _must(text, "from database import get_all_general_services", errors=errors)
    _must(text, "places = await get_places_by_service_with_likes(service_id)", errors=errors)
    _must(text, 'cb = f"place_{place[\'id\']}"', errors=errors)

    # Likes in catalog and detail card.
    _must(text, "likes_text = f\" ❤️{place['likes_count']}\" if place[\"likes_count\"] > 0 else \"\"", errors=errors)
    _must(text, "text += f\"❤️ <b>Лайків:</b> {likes_count}\\n\\n\"", errors=errors)
    _must(text, "rows.append([like_btn])", errors=errors)

    # Minimal baseline card controls.
    _must(text, "if business_enabled and place_enriched.get(\"is_verified\"):", errors=errors)
    _must(
        text,
        "rows.append([InlineKeyboardButton(text=\"⚠️ Запропонувати правку\", callback_data=f\"plrep_{place_id}\")])",
        errors=errors,
    )
    _must(text, "rows.append([InlineKeyboardButton(text=\"« Назад\", callback_data=f\"places_cat_{service_id}\")])", errors=errors)

    # Address + map path.
    _must(text, "text += f\"📍 <b>Адреса:</b> {place_enriched['address']}\\n\\n\"", errors=errors)
    _must(text, "map_file = get_map_file_for_address(place_enriched[\"address\"])", errors=errors)
    _must(text, "if map_file:", errors=errors)
    _must(text, "await message.answer_photo(", errors=errors)

    if errors:
        raise SystemExit("ERROR: business free resident baseline policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business free resident baseline policy smoke passed.")


if __name__ == "__main__":
    main()
