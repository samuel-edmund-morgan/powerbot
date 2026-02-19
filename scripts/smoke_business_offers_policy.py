#!/usr/bin/env python3
"""
Static smoke-check for Premium offers policy.

Policy:
- DB/repository/business profile supports two offer text fields and two optional offer image URLs.
- Owner edit flow exposes offer fields and routes through update_place_business_profile_field.
- Premium gating is enforced for offer fields.
- Resident place card renders offer block for pro/partner only.
"""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: file not found: {path}")
    return path.read_text(encoding="utf-8")


def _must(text: str, token: str, *, where: str, errors: list[str]) -> None:
    if token not in text:
        errors.append(f"{where}: missing `{token}`")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    db_text = _read(root / "src/database.py")
    repo_text = _read(root / "src/business/repository.py")
    service_text = _read(root / "src/business/service.py")
    business_handlers_text = _read(root / "src/business/handlers.py")
    resident_handlers_text = _read(root / "src/handlers.py")
    schema_text = _read(root / "schema.sql")
    errors: list[str] = []

    _must(schema_text, "offer_1_text", where="schema.sql", errors=errors)
    _must(schema_text, "offer_2_text", where="schema.sql", errors=errors)
    _must(schema_text, "offer_1_image_url", where="schema.sql", errors=errors)
    _must(schema_text, "offer_2_image_url", where="schema.sql", errors=errors)
    _must(db_text, "ALTER TABLE places ADD COLUMN offer_1_text", where="src/database.py", errors=errors)
    _must(db_text, "ALTER TABLE places ADD COLUMN offer_2_text", where="src/database.py", errors=errors)
    _must(db_text, "ALTER TABLE places ADD COLUMN offer_1_image_url", where="src/database.py", errors=errors)
    _must(db_text, "ALTER TABLE places ADD COLUMN offer_2_image_url", where="src/database.py", errors=errors)

    _must(repo_text, "offer_1_text", where="src/business/repository.py", errors=errors)
    _must(repo_text, "offer_2_text", where="src/business/repository.py", errors=errors)
    _must(repo_text, "offer_1_image_url", where="src/business/repository.py", errors=errors)
    _must(repo_text, "offer_2_image_url", where="src/business/repository.py", errors=errors)

    _must(service_text, "offer_1_text", where="src/business/service.py", errors=errors)
    _must(service_text, "offer_2_text", where="src/business/service.py", errors=errors)
    _must(service_text, "offer_1_image_url", where="src/business/service.py", errors=errors)
    _must(service_text, "offer_2_image_url", where="src/business/service.py", errors=errors)
    _must(service_text, "tier not in {\"pro\", \"partner\"}", where="src/business/service.py", errors=errors)

    _must(business_handlers_text, "bef:{place_id}:offer_1_text", where="src/business/handlers.py", errors=errors)
    _must(business_handlers_text, "bef:{place_id}:offer_2_text", where="src/business/handlers.py", errors=errors)
    _must(business_handlers_text, "bef:{place_id}:offer_1_image_url", where="src/business/handlers.py", errors=errors)
    _must(business_handlers_text, "bef:{place_id}:offer_2_image_url", where="src/business/handlers.py", errors=errors)
    _must(
        business_handlers_text,
        "\"offer_1_image_url\"",
        where="src/business/handlers.py",
        errors=errors,
    )
    _must(
        business_handlers_text,
        "\"offer_2_image_url\"",
        where="src/business/handlers.py",
        errors=errors,
    )
    _must(business_handlers_text, "🎁 Офер 1", where="src/business/handlers.py", errors=errors)
    _must(business_handlers_text, "🎁 Офер 2", where="src/business/handlers.py", errors=errors)
    _must(business_handlers_text, "🖼 Фото оферу 1", where="src/business/handlers.py", errors=errors)
    _must(business_handlers_text, "🖼 Фото оферу 2", where="src/business/handlers.py", errors=errors)

    _must(resident_handlers_text, "offer_1_text", where="src/handlers.py", errors=errors)
    _must(resident_handlers_text, "offer_2_text", where="src/handlers.py", errors=errors)
    _must(resident_handlers_text, "offer_1_image_url", where="src/handlers.py", errors=errors)
    _must(resident_handlers_text, "offer_2_image_url", where="src/handlers.py", errors=errors)
    _must(resident_handlers_text, "pmimg1_", where="src/handlers.py", errors=errors)
    _must(resident_handlers_text, "pmimg2_", where="src/handlers.py", errors=errors)
    _must(resident_handlers_text, "Акції та офери", where="src/handlers.py", errors=errors)
    _must(resident_handlers_text, "in {\"pro\", \"partner\"}", where="src/handlers.py", errors=errors)

    if errors:
        raise SystemExit("ERROR: business offers policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business offers policy smoke passed.")


if __name__ == "__main__":
    main()
