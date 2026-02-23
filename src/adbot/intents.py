"""Intent definitions for adbot matcher and inline contract."""

from __future__ import annotations

from dataclasses import dataclass

from inline_special_queries import (
    ADBOT_INLINE_QUERY_CONTRACT,
    INTENT_CAR_PASS,
    INTENT_ELECTRICIAN,
    INTENT_LIGHT_STATUS,
    INTENT_PARKING,
    INTENT_PLUMBER,
    INTENT_SECURITY,
)

INLINE_QUERY_BY_INTENT = dict(ADBOT_INLINE_QUERY_CONTRACT)


@dataclass(frozen=True)
class Intent:
    code: str
    title: str
    keywords: tuple[str, ...]
    inline_query: str
    fallback_reply: str
    required_signals: int = 2


INTENTS: tuple[Intent, ...] = (
    Intent(
        code=INTENT_ELECTRICIAN,
        title="Електрик",
        keywords=(
            "електрик",
            "номер",
            "телефон",
            "світло",
            "зникло",
            "вимк",
        ),
        inline_query=INLINE_QUERY_BY_INTENT[INTENT_ELECTRICIAN],
        fallback_reply="📞 Номер електрика: 067-576-22-42",
        required_signals=2,
    ),
    Intent(
        code=INTENT_PLUMBER,
        title="Сантехнік",
        keywords=("сантехн", "номер", "вода", "теч", "злив", "підтеч", "тече", "кран", "перевір", "душ"),
        # require two signals: common phrasing often includes "номер" + "сантехнік/вода"
        inline_query=INLINE_QUERY_BY_INTENT[INTENT_PLUMBER],
        fallback_reply="🛠 Зараз сантехніка в чаті не видно, зверніться в оголошення в адмін-чаті.",
        required_signals=2,
    ),
    Intent(
        code=INTENT_SECURITY,
        title="Охорона",
        keywords=("охорона", "охорон", "номер", "вишка", "сторож", "патруль", "цілодобово"),
        inline_query=INLINE_QUERY_BY_INTENT[INTENT_SECURITY],
        fallback_reply="🛡️ Охорона доступна цілодобово.",
        required_signals=2,
    ),
    Intent(
        code=INTENT_PARKING,
        title="Паркінг",
        keywords=("паркінг", "паркінгу", "машин", "павільйон", "номер"),
        inline_query=INLINE_QUERY_BY_INTENT[INTENT_PARKING],
        fallback_reply="🅿️ Для питань паркінгу відкрийте розділ «🚗 Оформлення...».",
        required_signals=2,
    ),
    Intent(
        code=INTENT_CAR_PASS,
        title="Перепустка",
        keywords=("перепуст", "пропуск", "пропуска", "прохід", "вхід", "номер", "член", "авто"),
        inline_query=INLINE_QUERY_BY_INTENT[INTENT_CAR_PASS],
        fallback_reply="🚗 Оформлення перепустки авто — через резидент-бота.",
        required_signals=2,
    ),
    Intent(
        code=INTENT_LIGHT_STATUS,
        title="Світло",
        keywords=("світло", "світла", "чи", "вимк", "відсутн", "зникло", "поточн"),
        inline_query=INLINE_QUERY_BY_INTENT[INTENT_LIGHT_STATUS],
        fallback_reply="💡 Щоб подивитись точний статус, відкрийте резидент-бота.",
        required_signals=2,
    ),
)


def get_intent(code: str) -> Intent | None:
    for intent in INTENTS:
        if intent.code == code:
            return intent
    return None
