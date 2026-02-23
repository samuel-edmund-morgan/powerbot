"""Shared resolver for inline special queries (services + light status)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


INTENT_ELECTRICIAN = "electrician"
INTENT_PLUMBER = "plumber"
INTENT_SECURITY = "security"
INTENT_PARKING = "parking"
INTENT_CAR_PASS = "car_pass"
INTENT_ELEVATOR = "elevator"
INTENT_ACCOUNTING = "accounting"
INTENT_LIGHT_STATUS = "light_status"


# Stable contract used by adbot and inline resolver in resident bot.
ADBOT_INLINE_QUERY_CONTRACT: tuple[tuple[str, str], ...] = (
    (INTENT_ELECTRICIAN, "електрик"),
    (INTENT_PLUMBER, "сантехнік"),
    (INTENT_SECURITY, "охорона"),
    (INTENT_PARKING, "паркінг"),
    (INTENT_CAR_PASS, "перепустка авто"),
    (INTENT_ELEVATOR, "диспетчер ліфтів"),
    (INTENT_ACCOUNTING, "бухгалтерія"),
    (INTENT_LIGHT_STATUS, "світло"),
)


@dataclass(frozen=True)
class InlineSpecialResult:
    result_id: str
    title: str
    description: str
    message_text: str
    disable_web_page_preview: bool = False


def _contains_any(query: str, tokens: tuple[str, ...]) -> bool:
    return any(token in query for token in tokens)


def _phone(cfg: Any | None, attr_name: str) -> str:
    value = getattr(cfg, attr_name, None) if cfg is not None else None
    return str(value).strip() if value else "не вказано"


def resolve_inline_special_result(query: str, *, cfg: Any | None = None) -> InlineSpecialResult | None:
    """Resolve inline query for special non-catalog intents.

    Returns None when query should be handled by catalog search.
    """
    q = (query or "").strip().lower()
    if not q:
        return None

    # Order matters: more specific intents first.
    if _contains_any(q, ("перепуст", "пропуск")):
        text = (
            "🚗 <b>Оформлення перепустки авто</b>\n\n"
            "1) Напишіть <b>@SkdNa12</b> в особисті повідомлення та надішліть фото договору "
            "оренди/власності, номер телефону та документ, що посвідчує особу.\n"
            "2) Додайте бота <b>@OhoronaSheriff_NA_bot</b> та введіть код активації.\n"
            "3) Після активації створюйте заявки на перепустки."
        )
        return InlineSpecialResult(
            result_id="service_car_pass",
            title="🚗 Перепустка авто",
            description="Як оформити перепустку для авто",
            message_text=text,
        )

    if _contains_any(q, ("ліфт", "ліфти", "диспетчер ліфт")):
        phones = _phone(cfg, "elevator_phones")
        if "," in phones:
            phone_lines = "\n".join(f"• <code>{p.strip()}</code>" for p in phones.split(",") if p.strip())
        else:
            phone_lines = f"• <code>{phones}</code>"
        text = (
            "🛗 <b>Диспетчер ліфтів (цілодобово)</b>\n\n"
            f"📞 Телефони:\n{phone_lines}\n\n"
            "Працює цілодобово."
        )
        return InlineSpecialResult(
            result_id="service_elevator",
            title="🛗 Диспетчер ліфтів",
            description="Цілодобова диспетчерська служба",
            message_text=text,
        )

    if _contains_any(q, ("бухгалтер", "бухгалтерія", "квитанц", "нарахув", "комунал")):
        text = (
            "🧾 <b>Бухгалтерія</b>\n\n"
            "📞 Телефони:\n"
            "• <code>044-300-12-45</code>\n"
            "• <code>067-558-35-77</code> (вайбер)"
        )
        return InlineSpecialResult(
            result_id="service_accounting",
            title="🧾 Бухгалтерія",
            description="Контакти бухгалтерії",
            message_text=text,
        )

    if _contains_any(q, ("паркінг", "паркинг")):
        text = (
            "🅿️ <b>Оренда паркінгу</b>\n\n"
            "📢 Канал оголошень мешканців:\n"
            "https://t.me/newengland_parking\n\n"
            "🌐 Онлайн-бронювання:\n"
            "https://parkspot.com.ua/catalog/nova-angliya"
        )
        return InlineSpecialResult(
            result_id="service_parking",
            title="🅿️ Паркінг",
            description="Оренда: канал оголошень + онлайн-бронювання",
            message_text=text,
            disable_web_page_preview=True,
        )

    if _contains_any(q, ("електрик",)):
        phone = _phone(cfg, "electrician_phone")
        return InlineSpecialResult(
            result_id="service_electrician",
            title="⚡ Електрик",
            description=f"📞 {phone}",
            message_text=(
                "⚡ <b>Електрик (цілодобово)</b>\n\n"
                f"📞 Телефон: <code>{phone}</code>\n\n"
                "Працює цілодобово."
            ),
        )

    if _contains_any(q, ("сантех", "сантехнік")):
        phone = _phone(cfg, "plumber_phone")
        return InlineSpecialResult(
            result_id="service_plumber",
            title="🔧 Сантехнік",
            description=f"📞 {phone}",
            message_text=(
                "🔧 <b>Сантехнік (цілодобово)</b>\n\n"
                f"📞 Телефон: <code>{phone}</code>\n\n"
                "Працює цілодобово."
            ),
        )

    if _contains_any(q, ("охорон", "охорона")):
        phone = _phone(cfg, "security_phone")
        return InlineSpecialResult(
            result_id="service_security",
            title="🛡️ Охорона",
            description=f"📞 {phone}",
            message_text=(
                "🛡️ <b>Охорона (цілодобово)</b>\n\n"
                f"📞 Телефон: <code>{phone}</code>\n\n"
                "Працює цілодобово."
            ),
        )

    if _contains_any(q, ("світло", "світла")):
        return InlineSpecialResult(
            result_id="light_status",
            title="💡 Статус світла",
            description="Поточний стан електропостачання",
            message_text=(
                "💡 <b>Статус світла</b>\n\n"
                "Точний статус залежить від будинку та секції.\n"
                "Відкрийте бота і оберіть будинок та секцію через «🏠 Обрати будинок»."
            ),
        )

    return None
