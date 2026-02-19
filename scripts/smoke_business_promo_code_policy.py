#!/usr/bin/env python3
"""
Static smoke-check for business promo-code format policy.

Policy:
- promo_code is validated by strict code-like regex.
- promo_code is normalized to uppercase before save.
- owner UI includes promo format hint.
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
    service_text = _read(root / "src/business/service.py")
    handlers_text = _read(root / "src/business/handlers.py")
    errors: list[str] = []

    _must(service_text, "PROMO_CODE_RE = re.compile(", where="src/business/service.py", errors=errors)
    _must(service_text, "PROMO_CODE_RE.fullmatch(clean_value)", where="src/business/service.py", errors=errors)
    _must(service_text, "clean_value = clean_value.upper()", where="src/business/service.py", errors=errors)
    _must(service_text, "Промокод: 2-32 символи", where="src/business/service.py", errors=errors)
    _must(handlers_text, "Формат: 2-32 символи", where="src/business/handlers.py", errors=errors)

    if errors:
        raise SystemExit("ERROR: business promo-code policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business promo-code policy smoke passed.")


if __name__ == "__main__":
    main()

