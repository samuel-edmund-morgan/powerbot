#!/usr/bin/env python3
"""
Static smoke-check: Partner QR-kit PDF contract.

Policy:
- requirements include `img2pdf` for PNG->PDF conversion.
- API server exposes `/api/v1/business/qr-kit/pdf` endpoint.
- endpoint returns `application/pdf` and uses `img2pdf.convert(...)`.
- businessbot QR-kit screen shows PDF buttons and uses API URL helper.
"""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: file not found: {path}")
    return path.read_text(encoding="utf-8")


def _must(text: str, token: str, *, errors: list[str], label: str) -> None:
    if token not in text:
        errors.append(f"{label}: missing token: {token}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    requirements = _read(root / "requirements.txt")
    api_server = _read(root / "src" / "api_server.py")
    business_handlers = _read(root / "src" / "business" / "handlers.py")

    errors: list[str] = []

    _must(requirements, "img2pdf", errors=errors, label="requirements.txt")

    _must(api_server, "async def business_qr_kit_pdf_handler(", errors=errors, label="src/api_server.py")
    _must(api_server, 'app.router.add_get("/api/v1/business/qr-kit/pdf", business_qr_kit_pdf_handler)', errors=errors, label="src/api_server.py")
    _must(api_server, "img2pdf.convert(", errors=errors, label="src/api_server.py")
    _must(api_server, "content_type=\"application/pdf\"", errors=errors, label="src/api_server.py")

    _must(business_handlers, "def _resident_place_qr_kit_pdf_url(", errors=errors, label="src/business/handlers.py")
    _must(business_handlers, "📄 PDF • Вхід", errors=errors, label="src/business/handlers.py")
    _must(business_handlers, "📄 PDF • Каса", errors=errors, label="src/business/handlers.py")
    _must(business_handlers, "📄 PDF • Столик", errors=errors, label="src/business/handlers.py")
    _must(business_handlers, "variant=\"entrance\"", errors=errors, label="src/business/handlers.py")
    _must(business_handlers, "variant=\"cashier\"", errors=errors, label="src/business/handlers.py")
    _must(business_handlers, "variant=\"table\"", errors=errors, label="src/business/handlers.py")

    if errors:
        raise SystemExit("ERROR: business QR-kit PDF policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business QR-kit PDF policy smoke passed.")


if __name__ == "__main__":
    main()
