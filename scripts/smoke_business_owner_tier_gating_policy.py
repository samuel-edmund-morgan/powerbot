#!/usr/bin/env python3
"""
Static smoke-check: business owner tier-gating policy.

Policy:
- Edit keyboard must show locked labels for Premium/Partner-only fields.
- Handlers must enforce tier guards for Premium/Partner field groups.
- QR voting access must be blocked for Free with redirect to plan menu.
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
    text = _read(root / "src" / "business" / "handlers.py")
    errors: list[str] = []

    # Locked labels in edit keyboard.
    _must(text, '"🔒 Меню/Прайс (Premium)"', errors=errors)
    _must(text, '"🔒 Замовити/Запис (Premium)"', errors=errors)
    _must(text, '"🔒 Офер 1 (Premium)"', errors=errors)
    _must(text, '"🔒 Офер 2 (Premium)"', errors=errors)
    _must(text, '"🔒 Фото оферу 1 (Premium)"', errors=errors)
    _must(text, '"🔒 Фото оферу 2 (Premium)"', errors=errors)
    _must(text, '"🔒 Фото 1 (Partner)"', errors=errors)
    _must(text, '"🔒 Фото 2 (Partner)"', errors=errors)
    _must(text, '"🔒 Фото 3 (Partner)"', errors=errors)
    _must(text, "premium_style = None if has_premium_access else STYLE_PRIMARY", errors=errors)
    _must(text, "partner_style = None if has_partner_access else STYLE_SUCCESS", errors=errors)
    _must(text, 'ikb(', errors=errors)
    _must(text, 'callback_data=f"bef:{place_id}:menu_url"', errors=errors)
    _must(text, "style=premium_style", errors=errors)
    _must(text, 'callback_data=f"bef:{place_id}:photo_1_url"', errors=errors)
    _must(text, "style=partner_style", errors=errors)

    # Owner card lock CTA for free.
    _must(text, 'edit_text = "✏️ Редагувати" if can_edit else f"🔒 Редагувати ({PLAN_TITLES[\'light\']})"', errors=errors)
    _must(text, 'qr_text = "🔳 QR голосування" if can_edit else f"🔒 QR голосування ({PLAN_TITLES[\'light\']})"', errors=errors)

    # Tier guards in edit field picker.
    _must(text, "if not _has_active_paid_subscription(item):", errors=errors)
    _must(text, 'await callback.answer("🔒 Редагування доступне з активним тарифом Light або вище.", show_alert=True)', errors=errors)
    _must(text, 'notice="🔒 Редагування картки доступне з активним тарифом Light або вище."', errors=errors)
    _must(text, 'if field in {', errors=errors)
    _must(text, '"menu_url",', errors=errors)
    _must(text, '"offer_1_image_url",', errors=errors)
    _must(text, "} and not _has_active_premium_subscription(item):", errors=errors)
    _must(text, 'await callback.answer("🔒 Ця опція доступна з активним Premium або Partner.", show_alert=True)', errors=errors)
    _must(text, 'notice="🔒 Premium-функції (меню/замовлення/офери/фото) доступні з Premium або Partner."', errors=errors)
    _must(text, 'if field in {"photo_1_url", "photo_2_url", "photo_3_url"} and not _has_active_partner_subscription(item):', errors=errors)
    _must(text, 'await callback.answer("🔒 Ця опція доступна з активним Partner.", show_alert=True)', errors=errors)
    _must(text, 'notice="🔒 Брендована галерея доступна з активним Partner."', errors=errors)

    # QR gating guard.
    _must(text, "if not _has_active_paid_subscription(item):", errors=errors)
    _must(text, 'await callback.answer("🔒 QR голосування доступний з активним тарифом Light або вище.", show_alert=True)', errors=errors)
    _must(text, 'notice="🔒 QR голосування доступний з активним тарифом Light або вище."', errors=errors)

    if errors:
        raise SystemExit("ERROR: business owner tier-gating policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business owner tier-gating policy smoke passed.")


if __name__ == "__main__":
    main()
