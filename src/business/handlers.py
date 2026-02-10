"""Handlers for standalone business bot runtime."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from business.service import (
    AccessDeniedError,
    BusinessCabinetService,
    NotFoundError,
    ValidationError,
)
from business.ui import bind_ui_message_id, render as ui_render, try_delete_user_message

router = Router()
cabinet_service = BusinessCabinetService()

BTN_ADD_BUSINESS = "➕ Додати бізнес"
BTN_CLAIM_BUSINESS = "🔗 Прив'язати бізнес"
BTN_MY_BUSINESSES = "🏢 Мої бізнеси"
BTN_PLANS = "💳 Плани"
# Legacy admin-only buttons/callbacks (admin features moved to adminbot).
BTN_MODERATION = "🛡 Модерація"
BTN_ADMIN_TOKENS = "🔐 Коди прив'язки"
BTN_CANCEL = "❌ Скасувати"

CB_MENU_HOME = "bmenu:home"
CB_MENU_ADD = "bmenu:add"
CB_MENU_ATTACH = "bmenu:attach"
CB_MENU_MINE = "bmenu:mine"
CB_MENU_PLANS = "bmenu:plans"
# Legacy admin-only menu callbacks (no longer shown in businessbot UI).
CB_MENU_MOD = "bmenu:moderation"
CB_MENU_TOKENS = "bmenu:tokens"
CB_MENU_CANCEL = "bmenu:cancel"

INTRO_TEXT = (
    "👋 <b>Бізнес-кабінет</b>\n\n"
    "Тут можна подати заявку на керування закладом, пройти модерацію, "
    "редагувати картку закладу і керувати тарифом.\n\n"
    "Оберіть дію:"
)

CB_MENU_NOOP = "bmenu:noop"
CB_CATEGORY_PICK_PREFIX = "bcat:"
CB_CATEGORY_PAGE_PREFIX = "bcatp:"
CB_BUILDING_PICK_PREFIX = "bbld:"
CB_BUILDING_CHANGE = "bbld_change"
CB_MY_PAGE_PREFIX = "bmy_p:"
CB_MY_OPEN_PREFIX = "bmy_o:"
CB_PLANS_PAGE_PREFIX = "bplans_p:"
CB_MOD_PAGE_PREFIX = "bmod_p:"
CB_MOD_APPROVE_PREFIX = "bmod_a:"
CB_MOD_REJECT_PREFIX = "bmod_r:"

CATEGORY_PAGE_SIZE = 10
CATEGORY_ROW_WIDTH = 2
BUILDING_ROW_WIDTH = 1
MY_BUSINESSES_PAGE_SIZE = 8
PLANS_PAGE_SIZE = 8
TOKEN_SERVICES_PAGE_SIZE = 10
TOKEN_PLACES_PAGE_SIZE = 8

CB_TOK_MENU = "btok:menu"
CB_TOK_LIST = "btok:list"
CB_TOK_GEN = "btok:gen"
CB_TOK_GEN_ALL = "btok:gen_all"
CB_TOK_GEN_ALL_CONFIRM = "btok:gen_all_confirm"

CB_TOKV_SVC_PICK_PREFIX = "btokv_s:"
CB_TOKV_SVC_PAGE_PREFIX = "btokv_sp:"
CB_TOKV_PLACE_PAGE_PREFIX = "btokv_pp:"
CB_TOKV_PLACE_OPEN_PREFIX = "btokv_o:"
CB_TOKV_PLACE_ROTATE_PREFIX = "btokv_r:"

CB_TOKG_SVC_PICK_PREFIX = "btokg_s:"
CB_TOKG_SVC_PAGE_PREFIX = "btokg_sp:"
CB_TOKG_PLACE_PAGE_PREFIX = "btokg_pp:"
CB_TOKG_PLACE_ROTATE_PREFIX = "btokg_r:"

PLAN_TITLES = {
    "free": "Free",
    "light": "Light",
    "pro": "Pro",
    "partner": "Partner",
}

OWNERSHIP_TITLES = {
    "approved": "✅ Підтверджено",
    "pending": "🕓 Очікує модерації",
    "rejected": "❌ Відхилено",
}

SUBSCRIPTION_TITLES = {
    "active": "🟢 Active",
    "inactive": "⚪ Inactive",
    "past_due": "🟠 Past Due",
    "canceled": "🔴 Canceled",
}


class AddBusinessStates(StatesGroup):
    waiting_category = State()
    waiting_name = State()
    waiting_description = State()
    waiting_building = State()
    waiting_address_details = State()


class ClaimStates(StatesGroup):
    waiting_token = State()


class EditPlaceStates(StatesGroup):
    waiting_value = State()


def build_main_menu(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=BTN_ADD_BUSINESS, callback_data=CB_MENU_ADD),
            InlineKeyboardButton(text=BTN_CLAIM_BUSINESS, callback_data=CB_MENU_ATTACH),
        ],
        [
            InlineKeyboardButton(text=BTN_MY_BUSINESSES, callback_data=CB_MENU_MINE),
            InlineKeyboardButton(text=BTN_PLANS, callback_data=CB_MENU_PLANS),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(
    (F.data == CB_MENU_MOD)
    | (F.data == CB_MENU_TOKENS)
    | F.data.startswith("bmod_")
    | F.data.startswith("bm:")
    | F.data.startswith("btok")
)
async def cb_legacy_admin_feature_moved(callback: CallbackQuery) -> None:
    """Admin flows were moved out of business bot; keep stale buttons safe."""
    try:
        await callback.answer("Цю адмін‑функцію перенесено в адмін‑бот.", show_alert=True)
    except Exception:
        pass
    if not callback.message:
        return
    user_id = callback.from_user.id if callback.from_user else callback.message.chat.id
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await ui_render(
        callback.message.bot,
        chat_id=callback.message.chat.id,
        prefer_message_id=callback.message.message_id,
        text=INTRO_TEXT,
        reply_markup=build_main_menu(user_id),
        remove_reply_keyboard=True,
    )


def build_cancel_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_MENU_CANCEL)],
        ]
    )


def build_category_keyboard(
    services: list[dict],
    *,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    buffer: list[InlineKeyboardButton] = []
    for svc in services:
        title = (svc.get("name") or "").strip() or f"ID {svc.get('id')}"
        buffer.append(
            InlineKeyboardButton(
                text=title,
                callback_data=f"{CB_CATEGORY_PICK_PREFIX}{int(svc['id'])}",
            )
        )
        if len(buffer) >= CATEGORY_ROW_WIDTH:
            rows.append(buffer)
            buffer = []
    if buffer:
        rows.append(buffer)

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"{CB_CATEGORY_PAGE_PREFIX}{page - 1}",
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data=CB_MENU_NOOP,
            )
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"{CB_CATEGORY_PAGE_PREFIX}{page + 1}",
                )
            )
        rows.append(nav)

    rows.append([InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_MENU_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_building_display(building: dict) -> str:
    name = str(building.get("name") or "").strip()
    addr = str(building.get("address") or "").strip()
    if not addr or addr == "-":
        return name or f"ID {building.get('id')}"
    return f"{name} ({addr})" if name else f"ID {building.get('id')}"


def build_building_keyboard(buildings: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    buffer: list[InlineKeyboardButton] = []
    for b in buildings:
        label = _format_building_display(b)
        buffer.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"{CB_BUILDING_PICK_PREFIX}{int(b['id'])}",
            )
        )
        if len(buffer) >= BUILDING_ROW_WIDTH:
            rows.append(buffer)
            buffer = []
    if buffer:
        rows.append(buffer)

    rows.append([InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_MENU_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _truncate_label(value: str, limit: int = 34) -> str:
    clean = (value or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def build_token_admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Список закладів", callback_data=CB_TOK_LIST)],
            [InlineKeyboardButton(text="♻️ Згенерувати коди", callback_data=CB_TOK_GEN)],
            [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
        ]
    )


def build_token_generate_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="♻️ Для всіх закладів", callback_data=CB_TOK_GEN_ALL)],
            [InlineKeyboardButton(text="🏢 Для закладу", callback_data=f"{CB_TOKG_SVC_PAGE_PREFIX}0")],
            [InlineKeyboardButton(text="« Коди прив'язки", callback_data=CB_TOK_MENU)],
            [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
        ]
    )


def _format_service_button(service: dict) -> str:
    name = str(service.get("name") or "").strip() or f"ID {service.get('id')}"
    count = service.get("place_count")
    if count is None:
        return _truncate_label(name, 30)
    try:
        count_int = int(count)
    except Exception:
        return _truncate_label(name, 30)
    return _truncate_label(f"{name} ({count_int})", 30)


def build_service_picker_keyboard(
    services: list[dict],
    *,
    page: int,
    total_pages: int,
    pick_prefix: str,
    page_prefix: str,
    section_back_text: str,
    section_back_callback_data: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    buffer: list[InlineKeyboardButton] = []
    for svc in services:
        label = _format_service_button(svc)
        buffer.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"{pick_prefix}{int(svc['id'])}",
            )
        )
        if len(buffer) >= CATEGORY_ROW_WIDTH:
            rows.append(buffer)
            buffer = []
    if buffer:
        rows.append(buffer)

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{page_prefix}{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data=CB_MENU_NOOP))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{page_prefix}{page + 1}"))
        rows.append(nav)

    rows.append([InlineKeyboardButton(text=section_back_text, callback_data=section_back_callback_data)])
    rows.append([InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_token_places_keyboard(
    places: list[dict],
    *,
    service_id: int,
    page: int,
    total_pages: int,
    open_prefix: str,
    page_prefix: str,
    back_callback_data: str,
    section_back_text: str,
    section_back_callback_data: str,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for item in places:
        place_id = int(item.get("id") or 0)
        name = str(item.get("name") or f"ID {place_id}")
        label = _truncate_label(name, 38)
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{open_prefix}{place_id}:{int(service_id)}:{int(page)}",
                )
            ]
        )

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{page_prefix}{int(service_id)}:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data=CB_MENU_NOOP))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{page_prefix}{int(service_id)}:{page + 1}"))
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="« Категорії", callback_data=back_callback_data)])
    buttons.append([InlineKeyboardButton(text=section_back_text, callback_data=section_back_callback_data)])
    buttons.append([InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_token_bulk_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Так, згенерувати", callback_data=CB_TOK_GEN_ALL_CONFIRM)],
            [InlineKeyboardButton(text="« Назад", callback_data=CB_TOK_GEN)],
            [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
        ]
    )


def build_my_businesses_keyboard(
    rows: list[dict],
    *,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for item in rows:
        owner_status = str(item.get("ownership_status") or "")
        status_icon = {"approved": "✅", "pending": "🕓", "rejected": "❌"}.get(owner_status, "•")
        verified_icon = "✅" if item.get("is_verified") else ""
        name = str(item.get("place_name") or f"ID {item.get('place_id')}")
        label = _truncate_label(f"{status_icon}{verified_icon} {name}".strip(), 38)
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{CB_MY_OPEN_PREFIX}{int(item['place_id'])}",
                )
            ]
        )

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"{CB_MY_PAGE_PREFIX}{page - 1}",
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data=CB_MENU_NOOP,
            )
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"{CB_MY_PAGE_PREFIX}{page + 1}",
                )
            )
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_plans_list_keyboard(
    rows: list[dict],
    *,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for item in rows:
        name = str(item.get("place_name") or f"ID {item.get('place_id')}")
        label = _truncate_label(f"💳 {name}".strip(), 38)
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"bp_menu:{int(item['place_id'])}:plans")])

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CB_PLANS_PAGE_PREFIX}{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data=CB_MENU_NOOP))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{CB_PLANS_PAGE_PREFIX}{page + 1}"))
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_moderation_queue_keyboard(owner_id: int, *, index: int, total: int) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="✅ Approve", callback_data=f"{CB_MOD_APPROVE_PREFIX}{owner_id}:{index}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"{CB_MOD_REJECT_PREFIX}{owner_id}:{index}"),
        ]
    ]
    if total > 1:
        nav: list[InlineKeyboardButton] = []
        if index > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CB_MOD_PAGE_PREFIX}{index - 1}"))
        nav.append(InlineKeyboardButton(text=f"{index + 1}/{total}", callback_data=CB_MENU_NOOP))
        if index < total - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{CB_MOD_PAGE_PREFIX}{index + 1}"))
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_edit_fields_keyboard(place_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Назва",
                    callback_data=f"bef:{place_id}:name",
                ),
                InlineKeyboardButton(
                    text="📝 Опис",
                    callback_data=f"bef:{place_id}:description",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📍 Адреса",
                    callback_data=f"bef:{place_id}:address",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="« Назад",
                    callback_data=f"{CB_MY_OPEN_PREFIX}{place_id}",
                )
            ],
            [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
        ]
    )


def build_plan_keyboard(
    place_id: int,
    current_tier: str,
    *,
    back_callback_data: str | None = None,
    source: str | None = None,
) -> InlineKeyboardMarkup:
    buttons = []
    first_row = []
    for tier in ("free", "light"):
        title = PLAN_TITLES[tier]
        if tier == current_tier:
            title = f"• {title}"
        cb = f"bp:{place_id}:{tier}:{source}" if source else f"bp:{place_id}:{tier}"
        first_row.append(InlineKeyboardButton(text=title, callback_data=cb))
    buttons.append(first_row)

    second_row = []
    for tier in ("pro", "partner"):
        title = PLAN_TITLES[tier]
        if tier == current_tier:
            title = f"• {title}"
        cb = f"bp:{place_id}:{tier}:{source}" if source else f"bp:{place_id}:{tier}"
        second_row.append(InlineKeyboardButton(text=title, callback_data=cb))
    buttons.append(second_row)
    back_cb = back_callback_data or f"{CB_MY_OPEN_PREFIX}{place_id}"
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])
    buttons.append([InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_moderation_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Approve", callback_data=f"bm:a:{owner_id}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"bm:r:{owner_id}"),
            ]
        ]
    )


def format_business_card(item: dict) -> str:
    place_name = html.escape(str(item.get("place_name") or "—"))
    place_address = html.escape(str(item.get("place_address") or "—"))
    owner_status = OWNERSHIP_TITLES.get(item["ownership_status"], item["ownership_status"])
    sub_status = SUBSCRIPTION_TITLES.get(item["subscription_status"], item["subscription_status"])
    tier = PLAN_TITLES.get(item["tier"], item["tier"])
    verified = "✅ Verified" if item["is_verified"] else "—"
    expires = item["subscription_expires_at"] or "—"
    return (
        f"🏢 <b>{place_name}</b> (ID: <code>{item['place_id']}</code>)\n"
        f"📍 {place_address}\n"
        f"📌 Статус власника: {owner_status}\n"
        f"💳 План: <b>{tier}</b>\n"
        f"🔁 Підписка: {sub_status}\n"
        f"✅ Verified: {verified}\n"
        f"⏳ Paid до: {expires}"
    )


async def notify_admins_about_owner_request(
    message: Message,
    owner_row: dict,
    place_row: dict | None,
    source: str,
) -> None:
    place_name = place_row["name"] if place_row else f"place_id={owner_row['place_id']}"
    if message.from_user:
        from_label = message.from_user.username or message.from_user.full_name
    else:
        from_label = str(owner_row["tg_user_id"])
    text = (
        "🛎 Нова заявка власника бізнесу\n\n"
        f"Request ID: <code>{owner_row['id']}</code>\n"
        f"Place: <b>{place_name}</b> (ID: <code>{owner_row['place_id']}</code>)\n"
        f"Telegram user: <code>{owner_row['tg_user_id']}</code>\n"
        f"From: {from_label}\n"
        f"Source: <code>{source}</code>\n"
        f"Created: {owner_row['created_at']}"
    )
    # Business admin actions are handled in the separate admin bot now.
    # We keep this notification so admins don't miss new requests.
    text += "\n\n⚙️ Модерація: відкрий <b>adminbot</b> → <b>Бізнес</b> → <b>Модерація</b>."
    keyboard = None
    for admin_id in cabinet_service.admin_ids:
        try:
            await message.bot.send_message(admin_id, text, reply_markup=keyboard)
        except Exception:
            continue


async def send_main_menu(message: Message, user_id: int) -> None:
    """Send main menu using inline keyboard only (no reply keyboard)."""
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        text=INTRO_TEXT,
        reply_markup=build_main_menu(user_id),
        remove_reply_keyboard=True,
        # /start or other command-based entry should always show something "new"
        # at the bottom of the chat, otherwise edits can be invisible.
        force_new_message=True,
    )


async def send_category_picker(
    message: Message,
    state: FSMContext,
    *,
    page: int = 0,
    prefer_message_id: int | None = None,
) -> None:
    services = await cabinet_service.repository.list_services()
    if not services:
        await state.clear()
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            prefer_message_id=prefer_message_id,
            text="Немає жодної категорії для вибору. Напиши адміністратору.\n\n" + INTRO_TEXT,
            reply_markup=build_main_menu(message.from_user.id if message.from_user else message.chat.id),
        )
        return

    total_pages = max(1, (len(services) + CATEGORY_PAGE_SIZE - 1) // CATEGORY_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    start = safe_page * CATEGORY_PAGE_SIZE
    chunk = services[start : start + CATEGORY_PAGE_SIZE]
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        prefer_message_id=prefer_message_id,
        text="Оберіть категорію:",
        reply_markup=build_category_keyboard(chunk, page=safe_page, total_pages=total_pages),
    )


async def send_building_picker(
    message: Message,
    state: FSMContext,
    *,
    prefer_message_id: int | None = None,
) -> None:
    buildings = await cabinet_service.repository.list_buildings()
    if not buildings:
        await state.clear()
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            prefer_message_id=prefer_message_id,
            text="Немає списку будинків. Напиши адміністратору.\n\n" + INTRO_TEXT,
            reply_markup=build_main_menu(message.from_user.id if message.from_user else message.chat.id),
        )
        return
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        prefer_message_id=prefer_message_id,
        text="Оберіть будинок:",
        reply_markup=build_building_keyboard(buildings),
    )


async def show_token_admin_menu(message: Message, *, prefer_message_id: int | None = None) -> None:
    admin_id = message.from_user.id if message.from_user else message.chat.id
    if not cabinet_service.is_admin(admin_id):
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            prefer_message_id=prefer_message_id,
            text="Ця дія доступна лише адміністратору.\n\n" + INTRO_TEXT,
            reply_markup=build_main_menu(admin_id),
        )
        return
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        prefer_message_id=prefer_message_id,
        text="🔐 <b>Коди прив'язки</b>\n\nОберіть дію:",
        reply_markup=build_token_admin_menu_keyboard(),
    )


async def show_token_services_view(message: Message, *, page: int = 0, prefer_message_id: int | None = None) -> None:
    admin_id = message.from_user.id if message.from_user else message.chat.id
    if not cabinet_service.is_admin(admin_id):
        await show_token_admin_menu(message, prefer_message_id=prefer_message_id)
        return
    services = await cabinet_service.repository.list_services_with_place_counts()
    if not services:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            prefer_message_id=prefer_message_id,
            text="Немає жодного закладу для прив'язки.\n\n" + INTRO_TEXT,
            reply_markup=build_main_menu(admin_id),
        )
        return
    total_pages = max(1, (len(services) + TOKEN_SERVICES_PAGE_SIZE - 1) // TOKEN_SERVICES_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    start = safe_page * TOKEN_SERVICES_PAGE_SIZE
    chunk = services[start : start + TOKEN_SERVICES_PAGE_SIZE]
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        prefer_message_id=prefer_message_id,
        text="Оберіть категорію:",
        reply_markup=build_service_picker_keyboard(
            chunk,
            page=safe_page,
            total_pages=total_pages,
            pick_prefix=CB_TOKV_SVC_PICK_PREFIX,
            page_prefix=CB_TOKV_SVC_PAGE_PREFIX,
            section_back_text="« Коди прив'язки",
            section_back_callback_data=CB_TOK_MENU,
        ),
    )


async def show_token_services_generate(
    message: Message,
    *,
    page: int = 0,
    prefer_message_id: int | None = None,
) -> None:
    admin_id = message.from_user.id if message.from_user else message.chat.id
    if not cabinet_service.is_admin(admin_id):
        await show_token_admin_menu(message, prefer_message_id=prefer_message_id)
        return
    services = await cabinet_service.repository.list_services_with_place_counts()
    if not services:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            prefer_message_id=prefer_message_id,
            text="Немає жодного закладу для генерації кодів.\n\n" + INTRO_TEXT,
            reply_markup=build_main_menu(admin_id),
        )
        return
    total_pages = max(1, (len(services) + TOKEN_SERVICES_PAGE_SIZE - 1) // TOKEN_SERVICES_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    start = safe_page * TOKEN_SERVICES_PAGE_SIZE
    chunk = services[start : start + TOKEN_SERVICES_PAGE_SIZE]
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        prefer_message_id=prefer_message_id,
        text="Оберіть категорію для генерації нового коду:",
        reply_markup=build_service_picker_keyboard(
            chunk,
            page=safe_page,
            total_pages=total_pages,
            pick_prefix=CB_TOKG_SVC_PICK_PREFIX,
            page_prefix=CB_TOKG_SVC_PAGE_PREFIX,
            section_back_text="« Генерація кодів",
            section_back_callback_data=CB_TOK_GEN,
        ),
    )


async def show_token_places_view(
    message: Message,
    service_id: int,
    *,
    page: int = 0,
    prefer_message_id: int | None = None,
) -> None:
    admin_id = message.from_user.id if message.from_user else message.chat.id
    if not cabinet_service.is_admin(admin_id):
        await show_token_admin_menu(message, prefer_message_id=prefer_message_id)
        return
    service = await cabinet_service.repository.get_service(service_id)
    if not service:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            prefer_message_id=prefer_message_id,
            text="Категорію не знайдено.\n\n" + INTRO_TEXT,
            reply_markup=build_main_menu(admin_id),
        )
        return
    total = await cabinet_service.repository.count_places_by_service(service_id)
    total_pages = max(1, (total + TOKEN_PLACES_PAGE_SIZE - 1) // TOKEN_PLACES_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    offset = safe_page * TOKEN_PLACES_PAGE_SIZE
    places = await cabinet_service.repository.list_places_by_service(
        service_id,
        limit=TOKEN_PLACES_PAGE_SIZE,
        offset=offset,
    )
    service_label = html.escape(str(service.get("name") or service_id))
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        prefer_message_id=prefer_message_id,
        text=f"Категорія: <b>{service_label}</b>\n\nОберіть заклад:",
        reply_markup=build_token_places_keyboard(
            places,
            service_id=service_id,
            page=safe_page,
            total_pages=total_pages,
            open_prefix=CB_TOKV_PLACE_OPEN_PREFIX,
            page_prefix=CB_TOKV_PLACE_PAGE_PREFIX,
            back_callback_data=f"{CB_TOKV_SVC_PAGE_PREFIX}0",
            section_back_text="« Коди прив'язки",
            section_back_callback_data=CB_TOK_MENU,
        ),
    )


async def show_token_places_generate(
    message: Message,
    service_id: int,
    *,
    page: int = 0,
    prefer_message_id: int | None = None,
) -> None:
    admin_id = message.from_user.id if message.from_user else message.chat.id
    if not cabinet_service.is_admin(admin_id):
        await show_token_admin_menu(message, prefer_message_id=prefer_message_id)
        return
    service = await cabinet_service.repository.get_service(service_id)
    if not service:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            prefer_message_id=prefer_message_id,
            text="Категорію не знайдено.\n\n" + INTRO_TEXT,
            reply_markup=build_main_menu(admin_id),
        )
        return
    total = await cabinet_service.repository.count_places_by_service(service_id)
    total_pages = max(1, (total + TOKEN_PLACES_PAGE_SIZE - 1) // TOKEN_PLACES_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    offset = safe_page * TOKEN_PLACES_PAGE_SIZE
    places = await cabinet_service.repository.list_places_by_service(
        service_id,
        limit=TOKEN_PLACES_PAGE_SIZE,
        offset=offset,
    )
    service_label = html.escape(str(service.get("name") or service_id))
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        prefer_message_id=prefer_message_id,
        text=f"Категорія: <b>{service_label}</b>\n\nОберіть заклад для генерації нового коду:",
        reply_markup=build_token_places_keyboard(
            places,
            service_id=service_id,
            page=safe_page,
            total_pages=total_pages,
            open_prefix=CB_TOKG_PLACE_ROTATE_PREFIX,
            page_prefix=CB_TOKG_PLACE_PAGE_PREFIX,
            back_callback_data=f"{CB_TOKG_SVC_PAGE_PREFIX}0",
            section_back_text="« Генерація кодів",
            section_back_callback_data=CB_TOK_GEN,
        ),
    )


async def show_token_details_view(
    message: Message,
    *,
    place_id: int,
    service_id: int,
    page: int,
    prefer_message_id: int | None = None,
) -> None:
    admin_id = message.from_user.id if message.from_user else message.chat.id
    try:
        result = await cabinet_service.get_or_create_active_claim_token_for_place(admin_id, place_id)
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            prefer_message_id=prefer_message_id,
            text=str(error) + "\n\n" + INTRO_TEXT,
            reply_markup=build_main_menu(admin_id),
        )
        return

    place = result["place"]
    token_row = result["token_row"] or {}
    place_name = html.escape(str(place.get("name") or place_id))
    token = html.escape(str(token_row.get("token") or "—"))
    expires_at = html.escape(str(token_row.get("expires_at") or "—"))

    text = (
        "🔐 <b>Код прив'язки</b>\n\n"
        f"Заклад: <b>{place_name}</b> (ID: <code>{place_id}</code>)\n"
        f"Код: <code>{token}</code>\n"
        f"Діє до: {expires_at}\n\n"
        "Важливо: старий код стає неактивним після генерації нового."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="♻️ Новий код",
                    callback_data=f"{CB_TOKV_PLACE_ROTATE_PREFIX}{place_id}:{service_id}:{page}",
                )
            ],
            [InlineKeyboardButton(text="« Назад", callback_data=f"{CB_TOKV_PLACE_PAGE_PREFIX}{service_id}:{page}")],
            [InlineKeyboardButton(text="« Коди прив'язки", callback_data=CB_TOK_MENU)],
            [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
        ]
    )
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        prefer_message_id=prefer_message_id,
        text=text,
        reply_markup=keyboard,
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id if message.from_user else message.chat.id
    await try_delete_user_message(message)
    await send_main_menu(message, user_id)


@router.message(Command("health"))
@router.message(F.text == "/health")
async def cmd_health(message: Message) -> None:
    await message.answer("ok")


@router.message(Command("cancel"))
@router.message(F.text == BTN_CANCEL)
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id if message.from_user else message.chat.id
    await try_delete_user_message(message)
    await send_main_menu(message, user_id)


@router.callback_query(F.data == CB_MENU_CANCEL)
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await ui_render(
            callback.message.bot,
            chat_id=callback.message.chat.id,
            prefer_message_id=callback.message.message_id,
            text=INTRO_TEXT,
            reply_markup=build_main_menu(callback.from_user.id),
        )
    await callback.answer()


@router.callback_query(F.data == CB_MENU_HOME)
async def cb_menu_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await ui_render(
            callback.message.bot,
            chat_id=callback.message.chat.id,
            prefer_message_id=callback.message.message_id,
            text=INTRO_TEXT,
            reply_markup=build_main_menu(callback.from_user.id),
        )
    await callback.answer()


@router.callback_query(F.data == CB_MENU_ADD)
async def cb_menu_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddBusinessStates.waiting_category)
    if callback.message:
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await send_category_picker(callback.message, state, prefer_message_id=callback.message.message_id)
    await callback.answer()


@router.callback_query(F.data == CB_MENU_ATTACH)
async def cb_menu_attach(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ClaimStates.waiting_token)
    if callback.message:
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await ui_render(
            callback.message.bot,
            chat_id=callback.message.chat.id,
            prefer_message_id=callback.message.message_id,
            text="Введи код прив'язки для прив'язки існуючого бізнесу.",
            reply_markup=build_cancel_menu(),
        )
    await callback.answer()


@router.callback_query(F.data == CB_MENU_MINE)
async def cb_menu_mine(callback: CallbackQuery) -> None:
    if callback.message:
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await show_my_businesses(callback.message, prefer_message_id=callback.message.message_id)
    await callback.answer()


@router.callback_query(F.data == CB_MENU_PLANS)
async def cb_menu_plans(callback: CallbackQuery) -> None:
    if callback.message:
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await show_plans_menu(callback.message, prefer_message_id=callback.message.message_id)
    await callback.answer()


@router.callback_query(F.data == CB_MENU_MOD)
async def cb_menu_moderation(callback: CallbackQuery) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return


@router.callback_query(F.data == CB_MENU_TOKENS)
async def cb_menu_tokens(callback: CallbackQuery, state: FSMContext) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return


@router.callback_query(F.data == CB_TOK_MENU)
async def cb_tok_menu(callback: CallbackQuery, state: FSMContext) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return


@router.callback_query(F.data == CB_TOK_LIST)
async def cb_tok_list(callback: CallbackQuery, state: FSMContext) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return


@router.callback_query(F.data == CB_TOK_GEN)
async def cb_tok_gen(callback: CallbackQuery, state: FSMContext) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return


@router.callback_query(F.data == CB_TOK_GEN_ALL)
async def cb_tok_gen_all(callback: CallbackQuery, state: FSMContext) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return


@router.callback_query(F.data == CB_TOK_GEN_ALL_CONFIRM)
async def cb_tok_gen_all_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return


@router.callback_query(F.data == CB_MENU_NOOP)
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith(CB_TOKV_SVC_PAGE_PREFIX))
async def cb_tokv_services_page(callback: CallbackQuery, state: FSMContext) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return
    if not callback.message:
        await callback.answer()
        return
    if not cabinet_service.is_admin(callback.from_user.id):
        await callback.answer("Доступ лише адміністратору.", show_alert=True)
        return
    try:
        page = int(callback.data.removeprefix(CB_TOKV_SVC_PAGE_PREFIX))
    except Exception:
        await callback.answer("Некоректна сторінка", show_alert=True)
        return
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await show_token_services_view(callback.message, page=page, prefer_message_id=callback.message.message_id)
    await callback.answer()


@router.callback_query(F.data.startswith(CB_TOKV_SVC_PICK_PREFIX))
async def cb_tokv_service_pick(callback: CallbackQuery, state: FSMContext) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return
    if not callback.message:
        await callback.answer()
        return
    if not cabinet_service.is_admin(callback.from_user.id):
        await callback.answer("Доступ лише адміністратору.", show_alert=True)
        return
    try:
        service_id = int(callback.data.removeprefix(CB_TOKV_SVC_PICK_PREFIX))
    except Exception:
        await callback.answer("Некоректна категорія", show_alert=True)
        return
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await show_token_places_view(callback.message, service_id, page=0, prefer_message_id=callback.message.message_id)
    await callback.answer()


@router.callback_query(F.data.startswith(CB_TOKV_PLACE_PAGE_PREFIX))
async def cb_tokv_places_page(callback: CallbackQuery, state: FSMContext) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return
    if not callback.message:
        await callback.answer()
        return
    if not cabinet_service.is_admin(callback.from_user.id):
        await callback.answer("Доступ лише адміністратору.", show_alert=True)
        return
    raw = callback.data.removeprefix(CB_TOKV_PLACE_PAGE_PREFIX)
    parts = [p for p in raw.split(":") if p]
    if len(parts) != 2:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    try:
        service_id = int(parts[0])
        page = int(parts[1])
    except Exception:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await show_token_places_view(callback.message, service_id, page=page, prefer_message_id=callback.message.message_id)
    await callback.answer()


@router.callback_query(F.data.startswith(CB_TOKV_PLACE_OPEN_PREFIX))
async def cb_tokv_place_open(callback: CallbackQuery, state: FSMContext) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return
    if not callback.message:
        await callback.answer()
        return
    if not cabinet_service.is_admin(callback.from_user.id):
        await callback.answer("Доступ лише адміністратору.", show_alert=True)
        return
    raw = callback.data.removeprefix(CB_TOKV_PLACE_OPEN_PREFIX)
    parts = [p for p in raw.split(":") if p]
    if len(parts) != 3:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    try:
        place_id = int(parts[0])
        service_id = int(parts[1])
        page = int(parts[2])
    except Exception:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await show_token_details_view(
        callback.message,
        place_id=place_id,
        service_id=service_id,
        page=page,
        prefer_message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_TOKV_PLACE_ROTATE_PREFIX))
async def cb_tokv_place_rotate(callback: CallbackQuery, state: FSMContext) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return
    if not callback.message:
        await callback.answer()
        return
    if not cabinet_service.is_admin(callback.from_user.id):
        await callback.answer("Доступ лише адміністратору.", show_alert=True)
        return
    raw = callback.data.removeprefix(CB_TOKV_PLACE_ROTATE_PREFIX)
    parts = [p for p in raw.split(":") if p]
    if len(parts) != 3:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    try:
        place_id = int(parts[0])
        service_id = int(parts[1])
        page = int(parts[2])
    except Exception:
        await callback.answer("Некоректні дані", show_alert=True)
        return

    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    try:
        rotated = await cabinet_service.rotate_claim_token_for_place(callback.from_user.id, place_id)
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    place_name = html.escape(str(rotated["place"].get("name") or place_id))
    token = html.escape(str(rotated.get("token") or "—"))
    expires_at = html.escape(str(rotated.get("expires_at") or "—"))
    text = (
        "✅ Новий код згенеровано.\n\n"
        f"Заклад: <b>{place_name}</b> (ID: <code>{place_id}</code>)\n"
        f"Код: <code>{token}</code>\n"
        f"Діє до: {expires_at}"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="♻️ Новий код",
                    callback_data=f"{CB_TOKV_PLACE_ROTATE_PREFIX}{place_id}:{service_id}:{page}",
                )
            ],
            [InlineKeyboardButton(text="« Назад", callback_data=f"{CB_TOKV_PLACE_PAGE_PREFIX}{service_id}:{page}")],
            [InlineKeyboardButton(text="« Коди прив'язки", callback_data=CB_TOK_MENU)],
            [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
        ]
    )
    await ui_render(
        callback.message.bot,
        chat_id=callback.message.chat.id,
        prefer_message_id=callback.message.message_id,
        text=text,
        reply_markup=keyboard,
    )
    await callback.answer("Готово")


@router.callback_query(F.data.startswith(CB_TOKG_SVC_PAGE_PREFIX))
async def cb_tokg_services_page(callback: CallbackQuery, state: FSMContext) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return
    if not callback.message:
        await callback.answer()
        return
    if not cabinet_service.is_admin(callback.from_user.id):
        await callback.answer("Доступ лише адміністратору.", show_alert=True)
        return
    try:
        page = int(callback.data.removeprefix(CB_TOKG_SVC_PAGE_PREFIX))
    except Exception:
        await callback.answer("Некоректна сторінка", show_alert=True)
        return
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await show_token_services_generate(callback.message, page=page, prefer_message_id=callback.message.message_id)
    await callback.answer()


@router.callback_query(F.data.startswith(CB_TOKG_SVC_PICK_PREFIX))
async def cb_tokg_service_pick(callback: CallbackQuery, state: FSMContext) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return
    if not callback.message:
        await callback.answer()
        return
    if not cabinet_service.is_admin(callback.from_user.id):
        await callback.answer("Доступ лише адміністратору.", show_alert=True)
        return
    try:
        service_id = int(callback.data.removeprefix(CB_TOKG_SVC_PICK_PREFIX))
    except Exception:
        await callback.answer("Некоректна категорія", show_alert=True)
        return
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await show_token_places_generate(callback.message, service_id, page=0, prefer_message_id=callback.message.message_id)
    await callback.answer()


@router.callback_query(F.data.startswith(CB_TOKG_PLACE_PAGE_PREFIX))
async def cb_tokg_places_page(callback: CallbackQuery, state: FSMContext) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return
    if not callback.message:
        await callback.answer()
        return
    if not cabinet_service.is_admin(callback.from_user.id):
        await callback.answer("Доступ лише адміністратору.", show_alert=True)
        return
    raw = callback.data.removeprefix(CB_TOKG_PLACE_PAGE_PREFIX)
    parts = [p for p in raw.split(":") if p]
    if len(parts) != 2:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    try:
        service_id = int(parts[0])
        page = int(parts[1])
    except Exception:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await show_token_places_generate(callback.message, service_id, page=page, prefer_message_id=callback.message.message_id)
    await callback.answer()


@router.callback_query(F.data.startswith(CB_TOKG_PLACE_ROTATE_PREFIX))
async def cb_tokg_place_rotate(callback: CallbackQuery, state: FSMContext) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return
    if not callback.message:
        await callback.answer()
        return
    if not cabinet_service.is_admin(callback.from_user.id):
        await callback.answer("Доступ лише адміністратору.", show_alert=True)
        return
    raw = callback.data.removeprefix(CB_TOKG_PLACE_ROTATE_PREFIX)
    parts = [p for p in raw.split(":") if p]
    if len(parts) != 3:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    try:
        place_id = int(parts[0])
        service_id = int(parts[1])
        page = int(parts[2])
    except Exception:
        await callback.answer("Некоректні дані", show_alert=True)
        return

    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    try:
        rotated = await cabinet_service.rotate_claim_token_for_place(callback.from_user.id, place_id)
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    place_name = html.escape(str(rotated["place"].get("name") or place_id))
    token = html.escape(str(rotated.get("token") or "—"))
    expires_at = html.escape(str(rotated.get("expires_at") or "—"))
    text = (
        "✅ Новий код згенеровано.\n\n"
        f"Заклад: <b>{place_name}</b> (ID: <code>{place_id}</code>)\n"
        f"Код: <code>{token}</code>\n"
        f"Діє до: {expires_at}\n\n"
        "Видай цей код власнику, щоб він міг прив'язати існуючий бізнес."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data=f"{CB_TOKG_PLACE_PAGE_PREFIX}{service_id}:{page}")],
            [InlineKeyboardButton(text="« Генерація кодів", callback_data=CB_TOK_GEN)],
            [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
        ]
    )
    await ui_render(
        callback.message.bot,
        chat_id=callback.message.chat.id,
        prefer_message_id=callback.message.message_id,
        text=text,
        reply_markup=keyboard,
    )
    await callback.answer("Готово")


@router.message(Command("new_business"))
@router.message(F.text == BTN_ADD_BUSINESS)
async def start_add_business(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddBusinessStates.waiting_category)
    await try_delete_user_message(message)
    await send_category_picker(message, state)


@router.callback_query(F.data.startswith(CB_CATEGORY_PAGE_PREFIX))
async def cb_category_page(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AddBusinessStates.waiting_category.state:
        await callback.answer("Це меню вже неактуальне. Натисни /start.", show_alert=True)
        return
    try:
        page = int(callback.data.removeprefix(CB_CATEGORY_PAGE_PREFIX))
    except Exception:
        await callback.answer("Некоректна сторінка", show_alert=True)
        return

    services = await cabinet_service.repository.list_services()
    if not services:
        await callback.answer("Немає категорій", show_alert=True)
        return
    total_pages = max(1, (len(services) + CATEGORY_PAGE_SIZE - 1) // CATEGORY_PAGE_SIZE)
    safe_page = max(0, min(page, total_pages - 1))
    start = safe_page * CATEGORY_PAGE_SIZE
    chunk = services[start : start + CATEGORY_PAGE_SIZE]
    if callback.message:
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await ui_render(
            callback.message.bot,
            chat_id=callback.message.chat.id,
            prefer_message_id=callback.message.message_id,
            text="Оберіть категорію:",
            reply_markup=build_category_keyboard(chunk, page=safe_page, total_pages=total_pages),
        )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_CATEGORY_PICK_PREFIX))
async def cb_category_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AddBusinessStates.waiting_category.state:
        await callback.answer("Це меню вже неактуальне. Натисни /start.", show_alert=True)
        return
    try:
        service_id = int(callback.data.removeprefix(CB_CATEGORY_PICK_PREFIX))
    except Exception:
        await callback.answer("Некоректна категорія", show_alert=True)
        return

    service = await cabinet_service.repository.get_service(service_id)
    if not service:
        await callback.answer("Категорію не знайдено", show_alert=True)
        return

    await state.update_data(service_id=service_id, service_name=service["name"])
    await state.set_state(AddBusinessStates.waiting_name)
    service_label = html.escape(service["name"])
    if callback.message:
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await ui_render(
            callback.message.bot,
            chat_id=callback.message.chat.id,
            prefer_message_id=callback.message.message_id,
            text=(
                f"Категорія: <b>{service_label}</b>\n"
                "Вкажи назву закладу."
            ),
            reply_markup=build_cancel_menu(),
        )
    await callback.answer()


@router.message(AddBusinessStates.waiting_category, F.text)
async def add_business_category(message: Message, state: FSMContext) -> None:
    await try_delete_user_message(message)
    await send_category_picker(message, state)


@router.message(AddBusinessStates.waiting_name, F.text)
async def add_business_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not name:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text="Назва не може бути порожньою.\n\nВкажи назву закладу.",
            reply_markup=build_cancel_menu(),
        )
        return
    await try_delete_user_message(message)
    await state.update_data(name=name)
    await state.set_state(AddBusinessStates.waiting_description)
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        text="Вкажи опис (або надішли '-' якщо без опису).",
        reply_markup=build_cancel_menu(),
    )


@router.message(AddBusinessStates.waiting_description, F.text)
async def add_business_description(message: Message, state: FSMContext) -> None:
    description = message.text.strip()
    if description == "-":
        description = ""
    await try_delete_user_message(message)
    await state.update_data(description=description)
    await state.set_state(AddBusinessStates.waiting_building)
    await send_building_picker(message, state)


@router.callback_query(F.data.startswith(CB_BUILDING_PICK_PREFIX))
async def cb_building_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AddBusinessStates.waiting_building.state:
        await callback.answer("Це меню вже неактуальне. Натисни /start.", show_alert=True)
        return
    try:
        building_id = int(callback.data.removeprefix(CB_BUILDING_PICK_PREFIX))
    except Exception:
        await callback.answer("Некоректний будинок", show_alert=True)
        return

    building = await cabinet_service.repository.get_building(building_id)
    if not building:
        await callback.answer("Будинок не знайдено", show_alert=True)
        return

    building_label = _format_building_display(building)
    await state.update_data(building_id=building_id, building_label=building_label)
    await state.set_state(AddBusinessStates.waiting_address_details)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_MENU_CANCEL)],
            [InlineKeyboardButton(text="⬅️ Змінити будинок", callback_data=CB_BUILDING_CHANGE)],
        ]
    )
    if callback.message:
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await ui_render(
            callback.message.bot,
            chat_id=callback.message.chat.id,
            prefer_message_id=callback.message.message_id,
            text=(
                f"Будинок: <b>{html.escape(building_label)}</b>\n"
                "Додай деталі адреси (орієнтир/поверх/під'їзд) або надішли '-' якщо без деталей."
            ),
            reply_markup=keyboard,
        )
    await callback.answer()


@router.callback_query(F.data == CB_BUILDING_CHANGE)
async def cb_building_change(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    # Allow changing building from the address-details step.
    await state.set_state(AddBusinessStates.waiting_building)
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await send_building_picker(callback.message, state, prefer_message_id=callback.message.message_id)
    await callback.answer()


@router.message(AddBusinessStates.waiting_building, F.text)
async def add_business_building_text(message: Message, state: FSMContext) -> None:
    # Users sometimes type instead of clicking; re-show picker.
    await try_delete_user_message(message)
    await send_building_picker(message, state)


@router.message(AddBusinessStates.waiting_address_details, F.text)
async def add_business_address_details(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    details = message.text.strip()
    if details == "-":
        details = ""

    building_label = str(data.get("building_label") or "").strip()
    address = building_label
    if details:
        address = f"{building_label}, {details}".strip(", ")

    try:
        service_id = int(data.get("service_id") or 0)
        result = await cabinet_service.register_new_business(
            tg_user_id=message.from_user.id if message.from_user else message.chat.id,
            service_id=service_id,
            place_name=data.get("name", ""),
            description=data.get("description", ""),
            address=address,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_MENU_CANCEL)],
                [InlineKeyboardButton(text="⬅️ Змінити будинок", callback_data=CB_BUILDING_CHANGE)],
            ]
        )
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text=str(error),
            reply_markup=keyboard,
        )
        return

    await try_delete_user_message(message)
    await state.clear()
    place = result["place"] or {}
    owner = result["owner"]
    place_name = html.escape(str(place.get("name") or owner["place_id"]))
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        text=(
            "✅ Заявку створено.\n\n"
            f"ID заявки: <code>{owner['id']}</code>\n"
            f"Заклад: <b>{place_name}</b>\n"
            "Статус: очікує модерації адміном.\n\n"
            "Оберіть дію:"
        ),
        reply_markup=build_main_menu(message.from_user.id if message.from_user else message.chat.id),
    )
    await notify_admins_about_owner_request(message, owner, place, source="new_business")


@router.message(Command("claim"))
@router.message(F.text == BTN_CLAIM_BUSINESS)
async def start_claim_business(message: Message, state: FSMContext) -> None:
    # Support both: /claim TOKEN and interactive token entry.
    if message.text and message.text.startswith("/claim "):
        token = message.text.split(maxsplit=1)[1].strip()
        await try_delete_user_message(message)
        await process_claim_token(message, state, token)
        return
    await state.clear()
    await state.set_state(ClaimStates.waiting_token)
    await try_delete_user_message(message)
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        text="Введи код прив'язки для прив'язки існуючого бізнесу.",
        reply_markup=build_cancel_menu(),
    )


@router.message(ClaimStates.waiting_token, F.text)
async def claim_wait_token(message: Message, state: FSMContext) -> None:
    await process_claim_token(message, state, message.text)


async def process_claim_token(message: Message, state: FSMContext, token: str) -> None:
    try:
        result = await cabinet_service.claim_business_by_token(
            tg_user_id=message.from_user.id if message.from_user else message.chat.id,
            token_raw=token,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text=str(error) + "\n\nВведи код прив'язки для прив'язки існуючого бізнесу.",
            reply_markup=build_cancel_menu(),
        )
        return
    await try_delete_user_message(message)
    await state.clear()
    owner = result["owner"]
    place = result["place"] or {}
    place_name = html.escape(str(place.get("name") or owner["place_id"]))
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        text=(
            "✅ Код прив'язки прийнято.\n\n"
            f"Заявка: <code>{owner['id']}</code>\n"
            f"Заклад: <b>{place_name}</b>\n"
            "Статус: очікує модерації адміном.\n\n"
            "Оберіть дію:"
        ),
        reply_markup=build_main_menu(message.from_user.id if message.from_user else message.chat.id),
    )
    await notify_admins_about_owner_request(message, owner, place, source="claim_token")


@router.message(Command("my_businesses"))
@router.message(F.text == BTN_MY_BUSINESSES)
async def show_my_businesses(
    message: Message,
    *,
    page: int = 0,
    prefer_message_id: int | None = None,
) -> None:
    # In private chats chat.id is the user id; callback.message.from_user is the bot.
    user_id = message.chat.id
    await try_delete_user_message(message)
    rows = await cabinet_service.list_user_businesses(user_id)
    if not rows:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            prefer_message_id=prefer_message_id,
            text=(
                "У тебе ще немає бізнесів у кабінеті.\n"
                f"Натисни «{BTN_ADD_BUSINESS}» або «{BTN_CLAIM_BUSINESS}».\n\n"
                "Оберіть дію:"
            ),
            reply_markup=build_main_menu(user_id),
        )
        return

    total_pages = max(1, (len(rows) + MY_BUSINESSES_PAGE_SIZE - 1) // MY_BUSINESSES_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    start = safe_page * MY_BUSINESSES_PAGE_SIZE
    chunk = rows[start : start + MY_BUSINESSES_PAGE_SIZE]
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        prefer_message_id=prefer_message_id,
        text="🏢 <b>Мої бізнеси</b>\n\nОберіть заклад:",
        reply_markup=build_my_businesses_keyboard(chunk, page=safe_page, total_pages=total_pages),
    )


@router.callback_query(F.data.startswith(CB_MY_PAGE_PREFIX))
async def cb_my_businesses_page(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer()
        return
    try:
        page = int(callback.data.removeprefix(CB_MY_PAGE_PREFIX))
    except Exception:
        await callback.answer("Некоректна сторінка", show_alert=True)
        return
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await show_my_businesses(callback.message, page=page, prefer_message_id=callback.message.message_id)
    await callback.answer()


@router.callback_query(F.data.startswith(CB_MY_OPEN_PREFIX))
async def cb_my_business_open(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer()
        return
    try:
        place_id = int(callback.data.removeprefix(CB_MY_OPEN_PREFIX))
    except Exception:
        await callback.answer("Некоректний заклад", show_alert=True)
        return

    user_id = callback.from_user.id
    rows = await cabinet_service.list_user_businesses(user_id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == place_id), None)
    if not item:
        await callback.answer("Недостатньо прав або заклад не знайдено.", show_alert=True)
        return

    keyboard_rows: list[list[InlineKeyboardButton]] = []
    if item.get("ownership_status") == "approved":
        keyboard_rows.append(
            [
                InlineKeyboardButton(text="✏️ Редагувати", callback_data=f"be:{place_id}"),
                InlineKeyboardButton(text="💳 Змінити план", callback_data=f"bp_menu:{place_id}"),
            ]
        )
    keyboard_rows.append([InlineKeyboardButton(text="« Мої бізнеси", callback_data=CB_MENU_MINE)])
    keyboard_rows.append([InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)])

    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await ui_render(
        callback.message.bot,
        chat_id=callback.message.chat.id,
        prefer_message_id=callback.message.message_id,
        text=format_business_card(item),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("be:"))
async def cb_edit_place(callback: CallbackQuery) -> None:
    payload = callback.data.split(":")
    if len(payload) != 2:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    place_id = int(payload[1])
    user_id = callback.from_user.id
    is_allowed = await cabinet_service.repository.is_approved_owner(user_id, place_id)
    if not is_allowed:
        await callback.answer("Доступ лише для підтвердженого owner.", show_alert=True)
        return
    if callback.message:
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await ui_render(
            callback.message.bot,
            chat_id=callback.message.chat.id,
            prefer_message_id=callback.message.message_id,
            text=f"Що редагуємо для place_id=<code>{place_id}</code>?",
            reply_markup=build_edit_fields_keyboard(place_id),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("bef:"))
async def cb_edit_field_pick(callback: CallbackQuery, state: FSMContext) -> None:
    payload = callback.data.split(":")
    if len(payload) != 3:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    place_id = int(payload[1])
    field = payload[2]
    is_allowed = await cabinet_service.repository.is_approved_owner(callback.from_user.id, place_id)
    if not is_allowed:
        await callback.answer("Доступ лише для підтвердженого owner.", show_alert=True)
        return
    field_label = {"name": "назву", "description": "опис", "address": "адресу"}.get(field, field)
    await state.set_state(EditPlaceStates.waiting_value)
    await state.update_data(place_id=place_id, field=field)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_MENU_CANCEL)],
            [InlineKeyboardButton(text="« Назад", callback_data=f"be:{place_id}")],
        ]
    )
    if callback.message:
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await ui_render(
            callback.message.bot,
            chat_id=callback.message.chat.id,
            prefer_message_id=callback.message.message_id,
            text=f"Надішли нову {field_label} для place_id=<code>{place_id}</code>.",
            reply_markup=keyboard,
        )
    await callback.answer()


@router.message(EditPlaceStates.waiting_value, F.text)
async def edit_place_apply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    place_id = int(data["place_id"])
    field = str(data["field"])
    try:
        updated_place = await cabinet_service.update_place_field(
            tg_user_id=message.from_user.id if message.from_user else message.chat.id,
            place_id=place_id,
            field=field,
            value=message.text,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text=str(error),
            reply_markup=build_cancel_menu(),
        )
        return
    await try_delete_user_message(message)
    await state.clear()
    rows = await cabinet_service.list_user_businesses(message.chat.id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == place_id), None)
    if not item:
        await send_main_menu(message, message.chat.id)
        return

    keyboard_rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="✏️ Редагувати", callback_data=f"be:{place_id}"),
            InlineKeyboardButton(text="💳 Змінити план", callback_data=f"bp_menu:{place_id}"),
        ],
        [InlineKeyboardButton(text="« Мої бізнеси", callback_data=CB_MENU_MINE)],
        [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
    ]
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        text="✅ Картку оновлено.\n\n" + format_business_card(item),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )


@router.message(Command("plans"))
@router.message(F.text == BTN_PLANS)
async def show_plans_menu(
    message: Message,
    *,
    page: int = 0,
    prefer_message_id: int | None = None,
) -> None:
    user_id = message.chat.id
    await try_delete_user_message(message)
    rows = await cabinet_service.list_user_businesses(user_id)
    approved = [row for row in rows if row["ownership_status"] == "approved"]
    if not approved:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            prefer_message_id=prefer_message_id,
            text="Немає підтверджених закладів для зміни плану.\n\n" + INTRO_TEXT,
            reply_markup=build_main_menu(user_id),
        )
        return

    total_pages = max(1, (len(approved) + PLANS_PAGE_SIZE - 1) // PLANS_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    start = safe_page * PLANS_PAGE_SIZE
    chunk = approved[start : start + PLANS_PAGE_SIZE]
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        prefer_message_id=prefer_message_id,
        text="💳 <b>Плани</b>\n\nОберіть заклад:",
        reply_markup=build_plans_list_keyboard(chunk, page=safe_page, total_pages=total_pages),
    )


@router.callback_query(F.data.startswith(CB_PLANS_PAGE_PREFIX))
async def cb_plans_page(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer()
        return
    try:
        page = int(callback.data.removeprefix(CB_PLANS_PAGE_PREFIX))
    except Exception:
        await callback.answer("Некоректна сторінка", show_alert=True)
        return
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await show_plans_menu(callback.message, page=page, prefer_message_id=callback.message.message_id)
    await callback.answer()


@router.callback_query(F.data.startswith("bp_menu:"))
async def cb_plan_menu(callback: CallbackQuery) -> None:
    payload = callback.data.split(":")
    if len(payload) not in (2, 3):
        await callback.answer("Некоректні дані", show_alert=True)
        return
    place_id = int(payload[1])
    source = payload[2] if len(payload) == 3 else "card"
    rows = await cabinet_service.list_user_businesses(callback.from_user.id)
    item = next((row for row in rows if row["place_id"] == place_id), None)
    if not item or item["ownership_status"] != "approved":
        await callback.answer("Недостатньо прав.", show_alert=True)
        return
    back_cb = CB_MENU_PLANS if source == "plans" else f"{CB_MY_OPEN_PREFIX}{place_id}"
    place_name = html.escape(str(item.get("place_name") or place_id))
    if callback.message:
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await ui_render(
            callback.message.bot,
            chat_id=callback.message.chat.id,
            prefer_message_id=callback.message.message_id,
            text=f"Обери тариф для <b>{place_name}</b>:",
            reply_markup=build_plan_keyboard(
                place_id,
                item["tier"],
                back_callback_data=back_cb,
                source=source,
            ),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("bp:"))
async def cb_change_plan(callback: CallbackQuery) -> None:
    payload = callback.data.split(":")
    if len(payload) not in (3, 4):
        await callback.answer("Некоректні дані", show_alert=True)
        return
    place_id = int(payload[1])
    tier = payload[2]
    source = payload[3] if len(payload) == 4 else "card"
    try:
        subscription = await cabinet_service.change_subscription_tier(
            tg_user_id=callback.from_user.id,
            place_id=place_id,
            tier=tier,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    if not callback.message:
        await callback.answer("Тариф оновлено")
        return

    rows = await cabinet_service.list_user_businesses(callback.from_user.id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == place_id), None)
    place_name = html.escape(str(item.get("place_name") if item else place_id))
    back_cb = CB_MENU_PLANS if source == "plans" else f"{CB_MY_OPEN_PREFIX}{place_id}"

    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await ui_render(
        callback.message.bot,
        chat_id=callback.message.chat.id,
        prefer_message_id=callback.message.message_id,
        text=f"✅ Тариф оновлено.\n\nОбери тариф для <b>{place_name}</b>:",
        reply_markup=build_plan_keyboard(
            place_id,
            subscription["tier"],
            back_callback_data=back_cb,
            source=source,
        ),
    )
    await callback.answer("Тариф оновлено")


@router.message(Command("moderation"))
async def show_moderation(
    message: Message,
    *,
    index: int = 0,
    prefer_message_id: int | None = None,
) -> None:
    # Admin features were moved to adminbot.
    user_id = message.from_user.id if message.from_user else message.chat.id
    await try_delete_user_message(message)
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        prefer_message_id=prefer_message_id,
        text="⚙️ Модерацію перенесено в <b>adminbot</b> → <b>Бізнес</b> → <b>Модерація</b>.\n\n" + INTRO_TEXT,
        reply_markup=build_main_menu(user_id),
        remove_reply_keyboard=True,
        force_new_message=True,
    )


@router.callback_query(F.data.startswith(CB_MOD_PAGE_PREFIX))
async def cb_moderation_page(callback: CallbackQuery) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return


@router.callback_query(F.data.startswith(CB_MOD_APPROVE_PREFIX))
async def cb_moderation_approve(callback: CallbackQuery) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return


@router.callback_query(F.data.startswith(CB_MOD_REJECT_PREFIX))
async def cb_moderation_reject(callback: CallbackQuery) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return


@router.callback_query(F.data.startswith("bm:"))
async def cb_moderate_owner(callback: CallbackQuery) -> None:
    # Admin features were moved to adminbot (legacy handler kept to avoid crashes).
    return


@router.message(Command("claim_token"))
async def cmd_claim_token(message: Message) -> None:
    # Admin features were moved to adminbot.
    user_id = message.from_user.id if message.from_user else message.chat.id
    await try_delete_user_message(message)
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        text="⚙️ Коди прив'язки перенесено в <b>adminbot</b> → <b>Бізнес</b> → <b>Коди прив'язки</b>.\n\n" + INTRO_TEXT,
        reply_markup=build_main_menu(user_id),
        remove_reply_keyboard=True,
        force_new_message=True,
    )
