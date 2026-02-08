"""Handlers for standalone business bot runtime."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

from business.service import (
    AccessDeniedError,
    BusinessCabinetService,
    NotFoundError,
    ValidationError,
)

router = Router()
cabinet_service = BusinessCabinetService()

BTN_ADD_BUSINESS = "➕ Додати бізнес"
BTN_CLAIM_BUSINESS = "🔗 Прив'язати бізнес"
BTN_MY_BUSINESSES = "🏢 Мої бізнеси"
BTN_PLANS = "💳 Плани"
BTN_MODERATION = "🛡 Модерація"
BTN_CANCEL = "❌ Скасувати"

CB_MENU_HOME = "bmenu:home"
CB_MENU_ADD = "bmenu:add"
CB_MENU_ATTACH = "bmenu:attach"
CB_MENU_MINE = "bmenu:mine"
CB_MENU_PLANS = "bmenu:plans"
CB_MENU_MOD = "bmenu:moderation"
CB_MENU_CANCEL = "bmenu:cancel"

INTRO_TEXT = (
    "👋 <b>Бізнес-кабінет</b>\n\n"
    "Тут можна подати заявку на керування закладом, пройти модерацію, "
    "редагувати картку закладу і керувати тарифом.\n\n"
    "Оберіть дію:"
)

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
    waiting_address = State()


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
    if cabinet_service.is_admin(user_id):
        rows.append([InlineKeyboardButton(text=BTN_MODERATION, callback_data=CB_MENU_MOD)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_cancel_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_MENU_CANCEL)],
        ]
    )


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
        ]
    )


def build_plan_keyboard(place_id: int, current_tier: str) -> InlineKeyboardMarkup:
    buttons = []
    first_row = []
    for tier in ("free", "light"):
        title = PLAN_TITLES[tier]
        if tier == current_tier:
            title = f"• {title}"
        first_row.append(InlineKeyboardButton(text=title, callback_data=f"bp:{place_id}:{tier}"))
    buttons.append(first_row)

    second_row = []
    for tier in ("pro", "partner"):
        title = PLAN_TITLES[tier]
        if tier == current_tier:
            title = f"• {title}"
        second_row.append(InlineKeyboardButton(text=title, callback_data=f"bp:{place_id}:{tier}"))
    buttons.append(second_row)
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
    owner_status = OWNERSHIP_TITLES.get(item["ownership_status"], item["ownership_status"])
    sub_status = SUBSCRIPTION_TITLES.get(item["subscription_status"], item["subscription_status"])
    tier = PLAN_TITLES.get(item["tier"], item["tier"])
    verified = "✅ Verified" if item["is_verified"] else "—"
    expires = item["subscription_expires_at"] or "—"
    return (
        f"🏢 <b>{item['place_name']}</b> (ID: <code>{item['place_id']}</code>)\n"
        f"📍 {item['place_address'] or '—'}\n"
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
    keyboard = build_moderation_keyboard(owner_row["id"])
    for admin_id in cabinet_service.admin_ids:
        try:
            await message.bot.send_message(admin_id, text, reply_markup=keyboard)
        except Exception:
            continue


async def _remove_reply_keyboard(message: Message) -> None:
    """Best-effort removal of legacy ReplyKeyboard without cluttering the chat."""
    try:
        tmp = await message.answer("…", reply_markup=ReplyKeyboardRemove())
    except Exception:
        return
    try:
        await tmp.delete()
    except Exception:
        # If we can't delete (permissions/time window), keep it minimal.
        pass


async def send_main_menu(message: Message, user_id: int) -> None:
    """Send main menu using inline keyboard only (no reply keyboard)."""
    await _remove_reply_keyboard(message)
    await message.answer(INTRO_TEXT, reply_markup=build_main_menu(user_id))


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id if message.from_user else message.chat.id
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
    await message.answer("Дію скасовано.")
    await send_main_menu(message, user_id)


@router.callback_query(F.data == CB_MENU_CANCEL)
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("Дію скасовано.")
    await send_main_menu(callback.message, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == CB_MENU_HOME)
async def cb_menu_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await send_main_menu(callback.message, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == CB_MENU_ADD)
async def cb_menu_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddBusinessStates.waiting_category)
    await callback.message.answer(
        "Вкажи категорію бізнесу (наприклад: Кафе та ресторани).",
        reply_markup=build_cancel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == CB_MENU_ATTACH)
async def cb_menu_attach(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ClaimStates.waiting_token)
    await callback.message.answer(
        "Введи код прив'язки для прив'язки існуючого бізнесу.",
        reply_markup=build_cancel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == CB_MENU_MINE)
async def cb_menu_mine(callback: CallbackQuery) -> None:
    await show_my_businesses(callback.message)
    await callback.answer()


@router.callback_query(F.data == CB_MENU_PLANS)
async def cb_menu_plans(callback: CallbackQuery) -> None:
    await show_plans_menu(callback.message)
    await callback.answer()


@router.callback_query(F.data == CB_MENU_MOD)
async def cb_menu_moderation(callback: CallbackQuery) -> None:
    await show_moderation(callback.message)
    await callback.answer()


@router.message(Command("new_business"))
@router.message(F.text == BTN_ADD_BUSINESS)
async def start_add_business(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddBusinessStates.waiting_category)
    await message.answer(
        "Вкажи категорію бізнесу (наприклад: Кафе та ресторани).",
        reply_markup=build_cancel_menu(),
    )


@router.message(AddBusinessStates.waiting_category, F.text)
async def add_business_category(message: Message, state: FSMContext) -> None:
    category = message.text.strip()
    if not category:
        await message.answer("Категорія не може бути порожньою.")
        return
    await state.update_data(category=category)
    await state.set_state(AddBusinessStates.waiting_name)
    await message.answer("Вкажи назву закладу.", reply_markup=build_cancel_menu())


@router.message(AddBusinessStates.waiting_name, F.text)
async def add_business_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not name:
        await message.answer("Назва не може бути порожньою.")
        return
    await state.update_data(name=name)
    await state.set_state(AddBusinessStates.waiting_description)
    await message.answer("Вкажи опис (або надішли '-' якщо без опису).", reply_markup=build_cancel_menu())


@router.message(AddBusinessStates.waiting_description, F.text)
async def add_business_description(message: Message, state: FSMContext) -> None:
    description = message.text.strip()
    if description == "-":
        description = ""
    await state.update_data(description=description)
    await state.set_state(AddBusinessStates.waiting_address)
    await message.answer("Вкажи адресу (або '-' якщо без адреси).", reply_markup=build_cancel_menu())


@router.message(AddBusinessStates.waiting_address, F.text)
async def add_business_address(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    address = message.text.strip()
    if address == "-":
        address = ""
    try:
        result = await cabinet_service.register_new_business(
            tg_user_id=message.from_user.id if message.from_user else message.chat.id,
            category_name=data.get("category", ""),
            place_name=data.get("name", ""),
            description=data.get("description", ""),
            address=address,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        await message.answer(str(error))
        return
    await state.clear()
    place = result["place"] or {}
    owner = result["owner"]
    await message.answer(
        "✅ Заявку створено.\n\n"
        f"ID заявки: <code>{owner['id']}</code>\n"
        f"Заклад: <b>{place.get('name', owner['place_id'])}</b>\n"
        "Статус: очікує модерації адміном.",
        reply_markup=build_main_menu(message.from_user.id if message.from_user else message.chat.id),
    )
    await notify_admins_about_owner_request(message, owner, place, source="new_business")


@router.message(Command("claim"))
@router.message(F.text == BTN_CLAIM_BUSINESS)
async def start_claim_business(message: Message, state: FSMContext) -> None:
    # Support both: /claim TOKEN and interactive token entry.
    if message.text and message.text.startswith("/claim "):
        token = message.text.split(maxsplit=1)[1].strip()
        await process_claim_token(message, state, token)
        return
    await state.clear()
    await state.set_state(ClaimStates.waiting_token)
    await message.answer(
        "Введи код прив'язки для прив'язки існуючого бізнесу.",
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
        await message.answer(str(error))
        return
    await state.clear()
    owner = result["owner"]
    place = result["place"] or {}
    await message.answer(
        "✅ Код прив'язки прийнято.\n\n"
        f"Заявка: <code>{owner['id']}</code>\n"
        f"Заклад: <b>{place.get('name', owner['place_id'])}</b>\n"
        "Статус: очікує модерації адміном.",
        reply_markup=build_main_menu(message.from_user.id if message.from_user else message.chat.id),
    )
    await notify_admins_about_owner_request(message, owner, place, source="claim_token")


@router.message(Command("my_businesses"))
@router.message(F.text == BTN_MY_BUSINESSES)
async def show_my_businesses(message: Message) -> None:
    # In private chats chat.id is the user id; callback.message.from_user is the bot.
    user_id = message.chat.id
    rows = await cabinet_service.list_user_businesses(user_id)
    if not rows:
        await message.answer(
            "У тебе ще немає бізнесів у кабінеті.\n"
            f"Натисни «{BTN_ADD_BUSINESS}» або «{BTN_CLAIM_BUSINESS}».",
            reply_markup=build_main_menu(user_id),
        )
        return

    await message.answer("Ось твої об'єкти:")
    for item in rows:
        text = format_business_card(item)
        if item["ownership_status"] == "approved":
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✏️ Редагувати",
                            callback_data=f"be:{item['place_id']}",
                        ),
                        InlineKeyboardButton(
                            text="💳 Змінити план",
                            callback_data=f"bp_menu:{item['place_id']}",
                        ),
                    ]
                ]
            )
        else:
            keyboard = None
        await message.answer(text, reply_markup=keyboard)


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
    await callback.message.answer(
        f"Що редагуємо для place_id=<code>{place_id}</code>?",
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
    await callback.message.answer(
        f"Надішли нову {field_label} для place_id=<code>{place_id}</code>.",
        reply_markup=build_cancel_menu(),
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
        await message.answer(str(error))
        return
    await state.clear()
    await message.answer(
        "✅ Картку оновлено.\n\n"
        f"🏢 <b>{updated_place['name']}</b>\n"
        f"📍 {updated_place['address'] or '—'}",
        reply_markup=build_main_menu(message.from_user.id if message.from_user else message.chat.id),
    )


@router.message(Command("plans"))
@router.message(F.text == BTN_PLANS)
async def show_plans_menu(message: Message) -> None:
    user_id = message.chat.id
    rows = await cabinet_service.list_user_businesses(user_id)
    approved = [row for row in rows if row["ownership_status"] == "approved"]
    if not approved:
        await message.answer("Немає підтверджених закладів для зміни плану.")
        return
    for item in approved:
        await message.answer(
            f"💳 <b>{item['place_name']}</b> (ID: <code>{item['place_id']}</code>)\n"
            f"Поточний тариф: <b>{PLAN_TITLES.get(item['tier'], item['tier'])}</b>",
            reply_markup=build_plan_keyboard(item["place_id"], item["tier"]),
        )


@router.callback_query(F.data.startswith("bp_menu:"))
async def cb_plan_menu(callback: CallbackQuery) -> None:
    payload = callback.data.split(":")
    if len(payload) != 2:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    place_id = int(payload[1])
    rows = await cabinet_service.list_user_businesses(callback.from_user.id)
    item = next((row for row in rows if row["place_id"] == place_id), None)
    if not item or item["ownership_status"] != "approved":
        await callback.answer("Недостатньо прав.", show_alert=True)
        return
    await callback.message.answer(
        f"Обери тариф для <b>{item['place_name']}</b>:",
        reply_markup=build_plan_keyboard(place_id, item["tier"]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bp:"))
async def cb_change_plan(callback: CallbackQuery) -> None:
    payload = callback.data.split(":")
    if len(payload) != 3:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    place_id = int(payload[1])
    tier = payload[2]
    try:
        subscription = await cabinet_service.change_subscription_tier(
            tg_user_id=callback.from_user.id,
            place_id=place_id,
            tier=tier,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    await callback.answer("Тариф оновлено")
    await callback.message.answer(
        "✅ Підписку оновлено.\n"
        f"Place ID: <code>{place_id}</code>\n"
        f"Tier: <b>{PLAN_TITLES.get(subscription['tier'], subscription['tier'])}</b>\n"
        f"Status: <b>{SUBSCRIPTION_TITLES.get(subscription['status'], subscription['status'])}</b>\n"
        f"Expires: {subscription['expires_at'] or '—'}",
    )


@router.message(Command("moderation"))
@router.message(F.text == BTN_MODERATION)
async def show_moderation(message: Message) -> None:
    admin_id = message.chat.id
    try:
        rows = await cabinet_service.list_pending_owner_requests(admin_id)
    except AccessDeniedError as error:
        await message.answer(str(error))
        return
    if not rows:
        await message.answer("Черга модерації порожня.")
        return
    await message.answer(f"У черзі: <b>{len(rows)}</b> заявок.")
    for item in rows:
        user_label = f"@{item['username']}" if item["username"] else (item["first_name"] or "unknown")
        await message.answer(
            "🧾 <b>Owner request</b>\n"
            f"Request ID: <code>{item['owner_id']}</code>\n"
            f"Place: <b>{item['place_name']}</b> (ID: <code>{item['place_id']}</code>)\n"
            f"Address: {item['place_address'] or '—'}\n"
            f"User: {user_label} / <code>{item['tg_user_id']}</code>\n"
            f"Created: {item['created_at']}",
            reply_markup=build_moderation_keyboard(item["owner_id"]),
        )


@router.callback_query(F.data.startswith("bm:"))
async def cb_moderate_owner(callback: CallbackQuery) -> None:
    payload = callback.data.split(":")
    if len(payload) != 3:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    action = payload[1]
    owner_id = int(payload[2])
    try:
        if action == "a":
            updated = await cabinet_service.approve_owner_request(callback.from_user.id, owner_id)
            action_label = "APPROVED"
            owner_msg = (
                "✅ Твою заявку на керування бізнесом підтверджено.\n"
                "Тепер доступні редагування і керування тарифом."
            )
        elif action == "r":
            updated = await cabinet_service.reject_owner_request(callback.from_user.id, owner_id)
            action_label = "REJECTED"
            owner_msg = "❌ Твою заявку на керування бізнесом відхилено адміністратором."
        else:
            await callback.answer("Невідома дія", show_alert=True)
            return
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return

    try:
        await callback.bot.send_message(
            updated["tg_user_id"],
            owner_msg,
            reply_markup=build_main_menu(updated["tg_user_id"]),
        )
    except Exception:
        pass

    base_text = callback.message.html_text or callback.message.text or "Owner request"
    updated_text = f"{base_text}\n\n<b>{action_label}</b> by <code>{callback.from_user.id}</code>"
    await callback.message.edit_text(updated_text, reply_markup=None)
    await callback.answer("Готово")


@router.message(Command("claim_token"))
async def cmd_claim_token(message: Message) -> None:
    text = (message.text or "").strip()
    parts = text.split()
    if len(parts) < 2:
        await message.answer("Використання: /claim_token <place_id> [ttl_hours]")
        return
    try:
        place_id = int(parts[1])
    except ValueError:
        await message.answer("place_id має бути числом.")
        return
    ttl_hours = 72
    if len(parts) >= 3:
        try:
            ttl_hours = int(parts[2])
        except ValueError:
            await message.answer("ttl_hours має бути числом.")
            return
    try:
        result = await cabinet_service.create_claim_token(
            admin_tg_user_id=message.from_user.id if message.from_user else message.chat.id,
            place_id=place_id,
            ttl_hours=ttl_hours,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        await message.answer(str(error))
        return
    await message.answer(
        "🔐 Код прив'язки згенеровано.\n\n"
        f"Place: <b>{result['place']['name']}</b>\n"
        f"Token: <code>{result['token']}</code>\n"
        f"Expires: {result['expires_at']}",
    )
