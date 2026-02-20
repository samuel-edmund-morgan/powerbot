#!/usr/bin/env python3
"""
Static smoke-check for Partner branded gallery policy.

Policy:
- DB/schema expose `photo_1_url..photo_3_url` fields.
- Owner edit flow exposes these fields.
- Service gates these fields to Partner tier.
- Resident card exposes partner photo callbacks.
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
    schema_text = _read(root / "schema.sql")
    db_text = _read(root / "src/database.py")
    repo_text = _read(root / "src/business/repository.py")
    service_text = _read(root / "src/business/service.py")
    business_handlers_text = _read(root / "src/business/handlers.py")
    resident_handlers_text = _read(root / "src/handlers.py")
    errors: list[str] = []

    for col in ("photo_1_url", "photo_2_url", "photo_3_url"):
        _must(schema_text, f"{col} TEXT DEFAULT NULL", where="schema.sql", errors=errors)
        _must(db_text, f"ALTER TABLE places ADD COLUMN {col} TEXT DEFAULT NULL", where="src/database.py", errors=errors)
        _must(repo_text, col, where="src/business/repository.py", errors=errors)
        _must(service_text, f"\"{col}\"", where="src/business/service.py", errors=errors)
        _must(business_handlers_text, f"bef:{{place_id}}:{col}", where="src/business/handlers.py", errors=errors)
    _must(business_handlers_text, "URL або file_id фото №1", where="src/business/handlers.py", errors=errors)
    _must(business_handlers_text, "URL або file_id фото №2", where="src/business/handlers.py", errors=errors)
    _must(business_handlers_text, "URL або file_id фото №3", where="src/business/handlers.py", errors=errors)

    _must(service_text, "tier != \"partner\"", where="src/business/service.py", errors=errors)
    _must(service_text, "доступна лише з активною підпискою Partner", where="src/business/service.py", errors=errors)

    _must(resident_handlers_text, 'callback_data=f"pph1_{place_id}"', where="src/handlers.py", errors=errors)
    _must(resident_handlers_text, 'callback_data=f"pph2_{place_id}"', where="src/handlers.py", errors=errors)
    _must(resident_handlers_text, 'callback_data=f"pph3_{place_id}"', where="src/handlers.py", errors=errors)
    _must(resident_handlers_text, 'await record_place_click(place_id, "partner_photo_1")', where="src/handlers.py", errors=errors)
    _must(resident_handlers_text, 'await record_place_click(place_id, "partner_photo_2")', where="src/handlers.py", errors=errors)
    _must(resident_handlers_text, 'await record_place_click(place_id, "partner_photo_3")', where="src/handlers.py", errors=errors)

    if errors:
        raise SystemExit("ERROR: business partner gallery policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business partner gallery policy smoke passed.")


if __name__ == "__main__":
    main()
