#!/usr/bin/env python3
"""
Static smoke-check for Light+ logo/photo field policy.

Policy:
- DB and schema provide `logo_url` in `places`.
- Business owner edit flow exposes `logo_url` field.
- Resident place card exposes logo CTA only through callback flow.
- Click analytics tracks `logo_open`.
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

    _must(schema_text, "logo_url TEXT DEFAULT NULL", where="schema.sql", errors=errors)
    _must(db_text, "ALTER TABLE places ADD COLUMN logo_url TEXT DEFAULT NULL", where="src/database.py", errors=errors)
    _must(db_text, "logo_url", where="src/database.py", errors=errors)

    _must(repo_text, "p.logo_url", where="src/business/repository.py", errors=errors)
    _must(repo_text, "place_logo_url", where="src/business/repository.py", errors=errors)
    _must(repo_text, "_maybe_set(\"logo_url\", logo_url)", where="src/business/repository.py", errors=errors)

    _must(service_text, '"logo_url"', where="src/business/service.py", errors=errors)

    _must(business_handlers_text, "bef:{place_id}:logo_url", where="src/business/handlers.py", errors=errors)
    _must(business_handlers_text, "URL або file_id логотипу/фото", where="src/business/handlers.py", errors=errors)
    _must(business_handlers_text, "Відкриття логотипу/фото", where="src/business/handlers.py", errors=errors)

    _must(resident_handlers_text, 'callback_data=f"plogo_{place_id}"', where="src/handlers.py", errors=errors)
    _must(resident_handlers_text, 'F.data.startswith("plogo_")', where="src/handlers.py", errors=errors)
    _must(resident_handlers_text, 'await record_place_click(place_id, "logo_open")', where="src/handlers.py", errors=errors)

    if errors:
        raise SystemExit("ERROR: business logo policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business logo policy smoke passed.")


if __name__ == "__main__":
    main()
