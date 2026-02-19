#!/usr/bin/env python3
"""
Static smoke-check for business contact validation policy.

Policy:
- contact "call" is normalized/validated as phone.
- contact "chat" is normalized/validated as @username / t.me/username.
- update_place_contact uses normalization helpers before DB write.
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

    _must(service_text, "def _normalize_phone_contact_value(", where="src/business/service.py", errors=errors)
    _must(service_text, "def _normalize_chat_contact_value(", where="src/business/service.py", errors=errors)
    _must(service_text, "TG_USERNAME_RE = re.compile(", where="src/business/service.py", errors=errors)
    _must(service_text, "Для кнопки «Подзвонити» вкажи коректний номер телефону.", where="src/business/service.py", errors=errors)
    _must(service_text, "Для кнопки «Написати» вкажи @username або t.me/username.", where="src/business/service.py", errors=errors)
    _must(service_text, "if ctype == \"call\":", where="src/business/service.py", errors=errors)
    _must(service_text, "cvalue_raw = _normalize_phone_contact_value(cvalue_raw)", where="src/business/service.py", errors=errors)
    _must(service_text, "cvalue_raw = _normalize_chat_contact_value(cvalue_raw)", where="src/business/service.py", errors=errors)

    # UI prompts should keep explicit format hint.
    _must(handlers_text, "Надішли @username або посилання на Telegram (t.me/...)", where="src/business/handlers.py", errors=errors)
    _must(handlers_text, "Надішли номер телефону (наприклад <code>+380671234567</code>)", where="src/business/handlers.py", errors=errors)

    if errors:
        raise SystemExit("ERROR: business contact validation policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business contact validation policy smoke passed.")


if __name__ == "__main__":
    main()

