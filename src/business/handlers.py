"""Handlers for standalone business bot runtime."""

from __future__ import annotations

from datetime import datetime, timezone
import html
import logging
from urllib.parse import quote_plus, urlencode, urlsplit, urlunsplit

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from config import CFG
from database import create_admin_job, create_place_report, create_business_support_request
from tg_buttons import STYLE_DANGER, STYLE_PRIMARY, STYLE_SUCCESS, ikb

from business.service import (
    AccessDeniedError,
    BusinessCabinetService,
    NotFoundError,
    PAYMENT_PROVIDER_MOCK,
    PAYMENT_PROVIDER_TELEGRAM_STARS,
    ValidationError,
)
from business.payments import SUBSCRIPTION_PERIOD_SECONDS
from business.plans import PAID_TIERS, PLAN_STARS_PRICES, PLAN_TITLES
from business.ui import (
    bind_ui_message_id,
    render as ui_render,
    try_delete_user_message,
)

logger = logging.getLogger(__name__)
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

CATEGORY_PAGE_SIZE = 10
CATEGORY_ROW_WIDTH = 2
BUILDING_ROW_WIDTH = 1
MY_BUSINESSES_PAGE_SIZE = 8
PLANS_PAGE_SIZE = 8
CB_EDIT_BUILDING_PICK_PREFIX = "bebld:"
CB_EDIT_BUILDING_CHANGE_PREFIX = "bebld_change:"
CB_PAYMENT_RESULT_PREFIX = "bpayr:"
CB_CONTACT_PICK_PREFIX = "bec:"
CB_CONTACT_CLEAR_PREFIX = "bec_clear:"
CB_GALLERY_MENU_PREFIX = "begal:"
CB_GALLERY_ADD_PREFIX = "begal_add:"
CB_GALLERY_DEL_PREFIX = "begal_del:"
CB_QR_OPEN_PREFIX = "bqr:"
CB_QR_KIT_OPEN_PREFIX = "bqrkit:"
CB_PARTNER_SUPPORT_PREFIX = "bps:"
CB_PARTNER_SUPPORT_CANCEL_PREFIX = "bpsc:"
CB_FREE_EDIT_REQUEST_PREFIX = "bfr:"
CB_FREE_EDIT_REQUEST_CANCEL_PREFIX = "bfrc:"

OWNERSHIP_TITLES = {
    "approved": "✅ Підтверджено",
    "pending": "🕓 Очікує модерації",
    "rejected": "❌ Відхилено",
}

SUBSCRIPTION_TITLES = {
    "active": "🟢 Активна",
    "inactive": "⚪ Неактивна",
    "past_due": "🟠 Потрібне продовження",
    "canceled": "🔴 Скасована",
}

def _parse_iso_utc(raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw_value))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _has_active_paid_subscription(item: dict) -> bool:
    tier = str(item.get("tier") or "free").strip().lower()
    status = str(item.get("subscription_status") or "inactive").strip().lower()
    if tier not in PAID_TIERS or status not in {"active", "canceled"}:
        return False
    expires_at = _parse_iso_utc(str(item.get("subscription_expires_at") or "").strip() or None)
    if not expires_at:
        return False
    return expires_at > datetime.now(timezone.utc)


def _has_active_premium_subscription(item: dict) -> bool:
    """Premium access for extra CTA fields (menu/order) is pro/partner only."""
    if not _has_active_paid_subscription(item):
        return False
    tier = str(item.get("tier") or "free").strip().lower()
    return tier in {"pro", "partner"}


def _has_active_partner_subscription(item: dict) -> bool:
    """Partner access for branded gallery fields."""
    if not _has_active_paid_subscription(item):
        return False
    tier = str(item.get("tier") or "free").strip().lower()
    return tier == "partner"


def _format_expires_short(raw_value: str | None) -> str:
    expires_at = _parse_iso_utc(raw_value)
    if not expires_at:
        return "—"
    return expires_at.strftime("%d.%m.%Y %H:%M UTC")


def _resident_place_deeplink(place_id: int) -> str | None:
    bot_username = str(CFG.bot_username or "").strip().lstrip("@")
    if not bot_username:
        return None
    return f"https://t.me/{bot_username}?start=place_{int(place_id)}"


def _resident_place_qr_url(place_id: int) -> str | None:
    deeplink = _resident_place_deeplink(place_id)
    if not deeplink:
        return None
    return f"https://api.qrserver.com/v1/create-qr-code/?size=600x600&data={quote_plus(deeplink)}"


def _resident_place_qr_kit_png_url(
    place_id: int,
    *,
    caption: str,
    size: int = 1200,
) -> str | None:
    deeplink = _resident_place_deeplink(place_id)
    if not deeplink:
        return None
    safe_size = max(300, min(int(size), 2000))
    safe_caption = str(caption or "").strip()
    return (
        "https://quickchart.io/qr"
        f"?text={quote_plus(deeplink)}"
        f"&size={safe_size}"
        f"&caption={quote_plus(safe_caption)}"
        "&dark=111827"
        "&light=ffffff"
        "&ecLevel=M"
        "&margin=1"
    )


def _resident_place_qr_kit_pdf_url(place_id: int, *, variant: str) -> str | None:
    """Build absolute URL for QR-kit PDF rendered by API server."""
    web_app_url = str(CFG.web_app_url or "").strip()
    if not web_app_url:
        return None
    parsed = urlsplit(web_app_url)
    if not parsed.scheme or not parsed.netloc:
        return None
    base_path = (parsed.path or "").rstrip("/")
    if base_path.endswith("/app"):
        base_path = base_path[: -len("/app")]
    endpoint_path = f"{base_path}/api/v1/business/qr-kit/pdf"
    query = urlencode({"place_id": int(place_id), "variant": str(variant or "").strip().lower()})
    return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, query, ""))


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
    waiting_address_building = State()
    waiting_address_details = State()
    waiting_contact_type = State()
    waiting_contact_value = State()
    waiting_gallery_media = State()


class FreeEditRequestStates(StatesGroup):
    waiting_text = State()


class PartnerSupportRequestStates(StatesGroup):
    waiting_text = State()


BUSINESS_PROFILE_FIELDS = {
    "opening_hours",
    "link_url",
    "logo_url",
    "promo_code",
    "menu_url",
    "order_url",
    "offer_1_text",
    "offer_2_text",
    "offer_1_image_url",
    "offer_2_image_url",
    "photo_1_url",
    "photo_2_url",
    "photo_3_url",
}

MEDIA_PROFILE_FIELDS = {
    "logo_url",
    "offer_1_image_url",
    "offer_2_image_url",
    "photo_1_url",
    "photo_2_url",
    "photo_3_url",
}


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


def build_free_edit_request_keyboard(place_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data=f"{CB_FREE_EDIT_REQUEST_CANCEL_PREFIX}{int(place_id)}")],
            [InlineKeyboardButton(text="« До закладу", callback_data=f"{CB_MY_OPEN_PREFIX}{int(place_id)}")],
            [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
        ]
    )


def build_partner_support_request_keyboard(place_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data=f"{CB_PARTNER_SUPPORT_CANCEL_PREFIX}{int(place_id)}")],
            [InlineKeyboardButton(text="« До закладу", callback_data=f"{CB_MY_OPEN_PREFIX}{int(place_id)}")],
            [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
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
        title = (svc.get("name") or "").strip() or "Без назви категорії"
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
        return name or "Будинок"
    return f"{name} ({addr})" if name else f"Будинок ({addr})"


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
        name = str(item.get("place_name") or "").strip() or "Заклад без назви"
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
        name = str(item.get("place_name") or "").strip() or "Заклад без назви"
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


def build_edit_fields_keyboard(
    place_id: int,
    *,
    has_premium_access: bool,
    has_partner_access: bool = False,
) -> InlineKeyboardMarkup:
    menu_text = "📋 Меню/Прайс" if has_premium_access else "🔒 Меню/Прайс (Premium)"
    order_text = "🛒 Замовити/Запис" if has_premium_access else "🔒 Замовити/Запис (Premium)"
    offer_1_text = "🎁 Офер 1" if has_premium_access else "🔒 Офер 1 (Premium)"
    offer_2_text = "🎁 Офер 2" if has_premium_access else "🔒 Офер 2 (Premium)"
    offer_1_image = "🖼 Фото оферу 1" if has_premium_access else "🔒 Фото оферу 1 (Premium)"
    offer_2_image = "🖼 Фото оферу 2" if has_premium_access else "🔒 Фото оферу 2 (Premium)"
    logo_text = "🖼 Логотип/фото"
    photo_1_text = "📸 Фото 1" if has_partner_access else "🔒 Фото 1 (Partner)"
    photo_2_text = "📸 Фото 2" if has_partner_access else "🔒 Фото 2 (Partner)"
    photo_3_text = "📸 Фото 3" if has_partner_access else "🔒 Фото 3 (Partner)"
    premium_style = None if has_premium_access else STYLE_PRIMARY
    partner_style = None if has_partner_access else STYLE_SUCCESS
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
                    text="⏰ Години",
                    callback_data=f"bef:{place_id}:opening_hours",
                ),
                InlineKeyboardButton(
                    text="📞 Контакт",
                    callback_data=f"bef:{place_id}:contact",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔗 Посилання",
                    callback_data=f"bef:{place_id}:link_url",
                ),
                InlineKeyboardButton(
                    text=logo_text,
                    callback_data=f"bef:{place_id}:logo_url",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🖼 Галерея",
                    callback_data=f"{CB_GALLERY_MENU_PREFIX}{place_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎟 Промокод",
                    callback_data=f"bef:{place_id}:promo_code",
                ),
            ],
            [
                ikb(
                    text=menu_text,
                    callback_data=f"bef:{place_id}:menu_url",
                    style=premium_style,
                ),
                ikb(
                    text=order_text,
                    callback_data=f"bef:{place_id}:order_url",
                    style=premium_style,
                ),
            ],
            [
                ikb(
                    text=offer_1_text,
                    callback_data=f"bef:{place_id}:offer_1_text",
                    style=premium_style,
                ),
                ikb(
                    text=offer_2_text,
                    callback_data=f"bef:{place_id}:offer_2_text",
                    style=premium_style,
                ),
            ],
            [
                ikb(
                    text=offer_1_image,
                    callback_data=f"bef:{place_id}:offer_1_image_url",
                    style=premium_style,
                ),
                ikb(
                    text=offer_2_image,
                    callback_data=f"bef:{place_id}:offer_2_image_url",
                    style=premium_style,
                ),
            ],
            [
                ikb(
                    text=photo_1_text,
                    callback_data=f"bef:{place_id}:photo_1_url",
                    style=partner_style,
                ),
                ikb(
                    text=photo_2_text,
                    callback_data=f"bef:{place_id}:photo_2_url",
                    style=partner_style,
                ),
            ],
            [
                ikb(
                    text=photo_3_text,
                    callback_data=f"bef:{place_id}:photo_3_url",
                    style=partner_style,
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


def build_edit_building_keyboard(buildings: list[dict], place_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    buffer: list[InlineKeyboardButton] = []
    for b in buildings:
        label = _format_building_display(b)
        buffer.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"{CB_EDIT_BUILDING_PICK_PREFIX}{int(place_id)}:{int(b['id'])}",
            )
        )
        if len(buffer) >= BUILDING_ROW_WIDTH:
            rows.append(buffer)
            buffer = []
    if buffer:
        rows.append(buffer)

    rows.append([InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_MENU_CANCEL)])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"be:{int(place_id)}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _truncate_media_ref(raw_value: str, max_len: int = 72) -> str:
    value = str(raw_value or "").strip()
    if len(value) <= max_len:
        return value
    return f"{value[: max_len - 1]}…"


def build_gallery_manage_keyboard(place_id: int, items: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="➕ Додати фото", callback_data=f"{CB_GALLERY_ADD_PREFIX}{int(place_id)}")],
    ]
    # Keep action list compact: show delete actions for first 8 items.
    for idx, item in enumerate(items[:8], start=1):
        media_id = int(item.get("id") or 0)
        if media_id <= 0:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 Видалити фото {idx}",
                    callback_data=f"{CB_GALLERY_DEL_PREFIX}{int(place_id)}:{media_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"be:{int(place_id)}")])
    rows.append([InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_plan_keyboard(
    place_id: int,
    current_tier: str,
    *,
    current_status: str = "inactive",
    current_expires_at: str | None = None,
    back_callback_data: str | None = None,
    source: str | None = None,
) -> InlineKeyboardMarkup:
    normalized_current = str(current_tier or "").strip().lower() or "free"
    normalized_status = str(current_status or "").strip().lower() or "inactive"
    expires_at = _parse_iso_utc(str(current_expires_at or "").strip() or None)
    is_paid_tier = normalized_current in PAID_TIERS
    has_paid_entitlement = (
        is_paid_tier
        and normalized_status in {"active", "canceled"}
        and bool(expires_at and expires_at > datetime.now(timezone.utc))
    )
    show_free_option = not (is_paid_tier and normalized_status in {"active", "canceled"})

    buttons = []
    first_row = []
    for tier in ("free", "light"):
        if tier == "free" and not show_free_option:
            if normalized_status == "active" and has_paid_entitlement:
                cancel_cb = f"bp_cancel:{place_id}:{source}" if source else f"bp_cancel:{place_id}"
                first_row.append(
                    ikb(
                        text="🚫 Скасувати автопродовження",
                        callback_data=cancel_cb,
                        style=STYLE_DANGER,
                    )
                )
            else:
                first_row.append(
                    ikb(
                        text="🚫 Автопродовження скасовано",
                        callback_data=CB_MENU_NOOP,
                        style=STYLE_DANGER,
                    )
                )
            continue

        title = PLAN_TITLES[tier]
        stars = PLAN_STARS_PRICES.get(tier)
        if stars:
            title = f"{title} ({stars}⭐)"
        if tier == normalized_current:
            title = f"• {title}"
        cb = f"bp:{place_id}:{tier}:{source}" if source else f"bp:{place_id}:{tier}"
        # Keep Light as default style to avoid "too salesy" UI; only highlight top tiers.
        first_row.append(ikb(text=title, callback_data=cb, style=None))
    buttons.append(first_row)

    second_row = []
    for tier in ("pro", "partner"):
        title = PLAN_TITLES[tier]
        stars = PLAN_STARS_PRICES.get(tier)
        if stars:
            title = f"{title} ({stars}⭐)"
        if tier == normalized_current:
            title = f"• {title}"
        cb = f"bp:{place_id}:{tier}:{source}" if source else f"bp:{place_id}:{tier}"
        style = STYLE_PRIMARY if tier == "pro" else STYLE_SUCCESS
        second_row.append(ikb(text=title, callback_data=cb, style=style))
    buttons.append(second_row)
    back_cb = back_callback_data or f"{CB_MY_OPEN_PREFIX}{place_id}"
    buttons.append([InlineKeyboardButton(text="« Назад", callback_data=back_cb)])
    buttons.append([InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_mock_payment_keyboard(
    *,
    place_id: int,
    tier: str,
    external_payment_id: str,
    source: str,
) -> InlineKeyboardMarkup:
    def cb(result: str) -> str:
        return f"{CB_PAYMENT_RESULT_PREFIX}{int(place_id)}:{tier}:{external_payment_id}:{result}:{source}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Імітувати успішну оплату", callback_data=cb("success"))],
            [InlineKeyboardButton(text="❌ Імітувати відміну", callback_data=cb("cancel"))],
            [InlineKeyboardButton(text="⚠️ Імітувати помилку", callback_data=cb("fail"))],
            [InlineKeyboardButton(text="« Назад", callback_data=f"bp_menu:{int(place_id)}:{source}")],
            [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
        ]
    )


def build_stars_payment_keyboard(
    *,
    pay_url: str,
    place_id: int,
    tier: str,
    source: str,
    back_callback_data: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатити підписку ⭐", url=str(pay_url))],
            [InlineKeyboardButton(text="🔄 Оновити посилання", callback_data=f"bp:{int(place_id)}:{tier}:{source}")],
            [InlineKeyboardButton(text="« Назад", callback_data=back_callback_data)],
            [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
        ]
    )


def format_business_card(item: dict) -> str:
    place_name = html.escape(str(item.get("place_name") or "—"))
    place_address = html.escape(str(item.get("place_address") or "—"))
    opening_hours = html.escape(str(item.get("place_opening_hours") or "").strip())
    contact_type = str(item.get("place_contact_type") or "").strip().lower()
    contact_value = html.escape(str(item.get("place_contact_value") or "").strip())
    link_url = html.escape(str(item.get("place_link_url") or "").strip())
    logo_url = html.escape(str(item.get("place_logo_url") or "").strip())
    photo_1_url = html.escape(str(item.get("place_photo_1_url") or "").strip())
    photo_2_url = html.escape(str(item.get("place_photo_2_url") or "").strip())
    photo_3_url = html.escape(str(item.get("place_photo_3_url") or "").strip())
    promo_code = html.escape(str(item.get("place_promo_code") or "").strip())
    menu_url = html.escape(str(item.get("place_menu_url") or "").strip())
    order_url = html.escape(str(item.get("place_order_url") or "").strip())
    offer_1_text = html.escape(str(item.get("place_offer_1_text") or "").strip())
    offer_2_text = html.escape(str(item.get("place_offer_2_text") or "").strip())
    offer_1_image_url = html.escape(str(item.get("place_offer_1_image_url") or "").strip())
    offer_2_image_url = html.escape(str(item.get("place_offer_2_image_url") or "").strip())
    owner_status = OWNERSHIP_TITLES.get(item["ownership_status"], item["ownership_status"])
    sub_status = SUBSCRIPTION_TITLES.get(item["subscription_status"], item["subscription_status"])
    tier = PLAN_TITLES.get(item["tier"], item["tier"])
    verified = "✅ Активна" if item["is_verified"] else "—"
    expires = item["subscription_expires_at"] or "—"
    profile_lines: list[str] = []
    if opening_hours:
        profile_lines.append(f"⏰ Години: {opening_hours}")
    if contact_value:
        label = "📞 Контакт" if contact_type == "call" else "💬 Контакт" if contact_type == "chat" else "📌 Контакт"
        profile_lines.append(f"{label}: {contact_value}")
    if link_url:
        profile_lines.append(f"🔗 Посилання: {link_url}")
    if logo_url:
        profile_lines.append(f"🖼 Логотип/фото: {logo_url}")
    if photo_1_url:
        profile_lines.append(f"📸 Фото 1: {photo_1_url}")
    if photo_2_url:
        profile_lines.append(f"📸 Фото 2: {photo_2_url}")
    if photo_3_url:
        profile_lines.append(f"📸 Фото 3: {photo_3_url}")
    if menu_url:
        profile_lines.append(f"📋 Меню/Прайс: {menu_url}")
    if order_url:
        profile_lines.append(f"🛒 Замовити/Запис: {order_url}")
    if offer_1_text:
        profile_lines.append(f"🎁 Офер 1: {offer_1_text}")
    if offer_2_text:
        profile_lines.append(f"🎁 Офер 2: {offer_2_text}")
    if offer_1_image_url:
        profile_lines.append(f"🖼 Фото оферу 1: {offer_1_image_url}")
    if offer_2_image_url:
        profile_lines.append(f"🖼 Фото оферу 2: {offer_2_image_url}")
    if promo_code:
        profile_lines.append(f"🎟 Промокод: <code>{promo_code}</code>")
    profile_block = ("\n" + "\n".join(profile_lines)) if profile_lines else ""

    return (
        f"🏢 <b>{place_name}</b>\n"
        f"📍 {place_address}\n"
        f"{profile_block}\n"
        f"📌 Статус доступу: {owner_status}\n"
        f"💳 Тариф: <b>{tier}</b>\n"
        f"🔁 Стан підписки: {sub_status}\n"
        f"🏅 Верифікація: {verified}\n"
        f"⏳ Активно до: {expires}"
    )


async def build_business_card_text(item: dict, *, days: int = 30) -> str:
    text = format_business_card(item)
    place_id = int(item.get("place_id") or 0)
    if place_id <= 0:
        return text
    try:
        views = await cabinet_service.repository.get_place_views_sum(place_id, days=int(days))
        coupon_opens = await cabinet_service.repository.get_place_clicks_sum(
            place_id,
            action="coupon_open",
            days=int(days),
        )
        chat_opens = await cabinet_service.repository.get_place_clicks_sum(
            place_id,
            action="chat",
            days=int(days),
        )
        call_opens = await cabinet_service.repository.get_place_clicks_sum(
            place_id,
            action="call",
            days=int(days),
        )
        link_opens = await cabinet_service.repository.get_place_clicks_sum(
            place_id,
            action="link",
            days=int(days),
        )
        menu_opens = await cabinet_service.repository.get_place_clicks_sum(
            place_id,
            action="menu",
            days=int(days),
        )
        order_opens = await cabinet_service.repository.get_place_clicks_sum(
            place_id,
            action="order",
            days=int(days),
        )
        logo_opens = await cabinet_service.repository.get_place_clicks_sum(
            place_id,
            action="logo_open",
            days=int(days),
        )
        gallery_opens = await cabinet_service.repository.get_place_clicks_sum(
            place_id,
            action="gallery_open",
            days=int(days),
        )
        partner_photo_1_opens = await cabinet_service.repository.get_place_clicks_sum(
            place_id,
            action="partner_photo_1",
            days=int(days),
        )
        partner_photo_2_opens = await cabinet_service.repository.get_place_clicks_sum(
            place_id,
            action="partner_photo_2",
            days=int(days),
        )
        partner_photo_3_opens = await cabinet_service.repository.get_place_clicks_sum(
            place_id,
            action="partner_photo_3",
            days=int(days),
        )
        offer_1_image_opens = await cabinet_service.repository.get_place_clicks_sum(
            place_id,
            action="offer1_image",
            days=int(days),
        )
        offer_2_image_opens = await cabinet_service.repository.get_place_clicks_sum(
            place_id,
            action="offer2_image",
            days=int(days),
        )
    except Exception:
        logger.exception("Failed to load place activity stats place_id=%s", place_id)
        return text
    total_cta_clicks = (
        int(coupon_opens)
        + int(chat_opens)
        + int(call_opens)
        + int(link_opens)
        + int(menu_opens)
        + int(order_opens)
        + int(logo_opens)
        + int(gallery_opens)
        + int(partner_photo_1_opens)
        + int(partner_photo_2_opens)
        + int(partner_photo_3_opens)
        + int(offer_1_image_opens)
        + int(offer_2_image_opens)
    )
    ctr_pct = round((total_cta_clicks * 100.0) / int(views), 1) if int(views) > 0 else 0.0
    text += (
        f"\n\n📊 Активність за {int(days)} днів\n"
        f"• Перегляди картки: <b>{int(views)}</b>\n"
        f"• Відкриття промокоду: <b>{int(coupon_opens)}</b>\n"
        f"• Відкриття чату: <b>{int(chat_opens)}</b>\n"
        f"• Відкриття дзвінка: <b>{int(call_opens)}</b>\n"
        f"• Відкриття посилання: <b>{int(link_opens)}</b>\n"
        f"• Відкриття меню/прайсу: <b>{int(menu_opens)}</b>\n"
        f"• Відкриття замовлення/запису: <b>{int(order_opens)}</b>\n"
        f"• Відкриття логотипу/фото: <b>{int(logo_opens)}</b>\n"
        f"• Відкриття галереї: <b>{int(gallery_opens)}</b>\n"
        f"• Відкриття фото 1 (Partner): <b>{int(partner_photo_1_opens)}</b>\n"
        f"• Відкриття фото 2 (Partner): <b>{int(partner_photo_2_opens)}</b>\n"
        f"• Відкриття фото 3 (Partner): <b>{int(partner_photo_3_opens)}</b>\n"
        f"• Відкриття фото оферу 1: <b>{int(offer_1_image_opens)}</b>\n"
        f"• Відкриття фото оферу 2: <b>{int(offer_2_image_opens)}</b>\n"
        f"• Усі кліки по кнопках: <b>{int(total_cta_clicks)}</b>\n"
        f"• CTR кнопок: <b>{ctr_pct}%</b>"
    )
    if _has_active_premium_subscription(item):
        try:
            daily_rows = await cabinet_service.repository.get_place_activity_daily(place_id, days=7)
        except Exception:
            logger.exception("Failed to load place daily activity stats place_id=%s", place_id)
            return text
        if daily_rows:
            day_lines: list[str] = []
            for row in daily_rows:
                raw_day = str(row.get("day") or "").strip()
                try:
                    dt = datetime.fromisoformat(raw_day)
                    day_label = dt.strftime("%d.%m")
                except Exception:
                    day_label = raw_day
                day_lines.append(
                    f"• {day_label}: 👁 {int(row.get('views') or 0)} | 🎟 {int(row.get('coupon_opens') or 0)} | 🎯 {int(row.get('clicks') or 0)} | CTR {float(row.get('ctr') or 0):.1f}%"
                )
            text += "\n\n📈 По днях (7 днів)\n" + "\n".join(day_lines)
    return text


async def notify_admins_about_owner_request(
    message: Message,
    owner_row: dict,
    place_row: dict | None,
    source: str,
) -> None:
    place_name_raw = str(place_row["name"]) if place_row and place_row.get("name") else "Невідомий заклад"
    from_username = ""
    from_first_name = ""
    from_last_name = ""
    from_full_name = ""
    if message.from_user:
        from_username = str(message.from_user.username or "")
        from_first_name = str(message.from_user.first_name or "")
        from_last_name = str(message.from_user.last_name or "")
        from_full_name = str(message.from_user.full_name or "").strip()
        from_label_raw = str(message.from_user.username or from_full_name or owner_row["tg_user_id"])
    else:
        from_label_raw = str(owner_row["tg_user_id"])

    payload = {
        "request_id": int(owner_row["id"]),
        "place_id": int(owner_row["place_id"]),
        "place_name": place_name_raw,
        "owner_tg_user_id": int(owner_row["tg_user_id"]),
        "from_label": from_label_raw,
        "from_username": from_username,
        "from_first_name": from_first_name,
        "from_last_name": from_last_name,
        "from_full_name": from_full_name,
        "source": str(source),
        "created_at": str(owner_row.get("created_at") or ""),
    }

    try:
        await create_admin_job("admin_owner_request_alert", payload, created_by=int(owner_row["tg_user_id"]))
    except Exception:
        logger.exception(
            "Failed to enqueue admin_owner_request_alert for request_id=%s place_id=%s",
            owner_row.get("id"),
            owner_row.get("place_id"),
        )


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


async def send_edit_building_picker(
    message: Message,
    state: FSMContext,
    *,
    place_id: int,
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
        text="Оберіть будинок для нової адреси:",
        reply_markup=build_edit_building_keyboard(buildings, place_id),
    )


def _build_owner_place_card_action_rows(*, place_id: int, item: dict) -> list[list[InlineKeyboardButton]]:
    """Build owner place-card action rows with a single source of truth."""
    rows: list[list[InlineKeyboardButton]] = []
    if item.get("ownership_status") != "approved":
        return rows

    can_edit = _has_active_paid_subscription(item)
    edit_text = "✏️ Редагувати" if can_edit else f"🔒 Редагувати ({PLAN_TITLES['light']})"
    edit_btn = (
        InlineKeyboardButton(text=edit_text, callback_data=f"be:{place_id}")
        if can_edit
        else ikb(text=edit_text, callback_data=f"be:{place_id}", style=STYLE_PRIMARY)
    )
    rows.append(
        [
            edit_btn,
            ikb(text="💳 Змінити план", callback_data=f"bp_menu:{place_id}", style=STYLE_PRIMARY),
        ]
    )
    if not can_edit:
        rows.append([ikb(text="📝 Запропонувати правку", callback_data=f"{CB_FREE_EDIT_REQUEST_PREFIX}{place_id}", style=STYLE_PRIMARY)])

    qr_text = "🔳 QR голосування" if can_edit else f"🔒 QR голосування ({PLAN_TITLES['light']})"
    qr_btn = (
        InlineKeyboardButton(text=qr_text, callback_data=f"{CB_QR_OPEN_PREFIX}{place_id}")
        if can_edit
        else ikb(text=qr_text, callback_data=f"{CB_QR_OPEN_PREFIX}{place_id}", style=STYLE_PRIMARY)
    )
    rows.append([qr_btn])

    can_partner_qr_kit = _has_active_partner_subscription(item)
    qr_kit_text = "🪧 QR-комплект" if can_partner_qr_kit else f"🔒 QR-комплект ({PLAN_TITLES['partner']})"
    qr_kit_cb = f"{CB_QR_KIT_OPEN_PREFIX}{place_id}"
    qr_kit_btn = (
        InlineKeyboardButton(text=qr_kit_text, callback_data=qr_kit_cb)
        if can_partner_qr_kit
        else ikb(text=qr_kit_text, callback_data=qr_kit_cb, style=STYLE_SUCCESS)
    )
    rows.append([qr_kit_btn])

    support_text = (
        "🧑‍💼 Пріоритетна підтримка" if can_partner_qr_kit else f"🔒 Пріоритетна підтримка ({PLAN_TITLES['partner']})"
    )
    support_cb = f"{CB_PARTNER_SUPPORT_PREFIX}{place_id}"
    support_btn = (
        InlineKeyboardButton(text=support_text, callback_data=support_cb)
        if can_partner_qr_kit
        else ikb(text=support_text, callback_data=support_cb, style=STYLE_SUCCESS)
    )
    rows.append([support_btn])
    return rows


async def render_place_card_updated(message: Message, *, place_id: int, note_text: str) -> None:
    rows = await cabinet_service.list_user_businesses(message.chat.id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == int(place_id)), None)
    if not item:
        await send_main_menu(message, message.chat.id)
        return
    keyboard_rows = _build_owner_place_card_action_rows(place_id=place_id, item=item)
    keyboard_rows.append([InlineKeyboardButton(text="« Мої бізнеси", callback_data=CB_MENU_MINE)])
    keyboard_rows.append([InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)])
    card_text = await build_business_card_text(item)
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        text=f"{note_text}\n\n{card_text}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
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


@router.callback_query(F.data == CB_MENU_NOOP)
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


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
    place_name = html.escape(str(place.get("name") or "Ваш заклад"))
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        text=(
            "✅ Заявку створено.\n\n"
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
    place_name = html.escape(str(place.get("name") or "Ваш заклад"))
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        text=(
            "✅ Код прив'язки прийнято.\n\n"
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

    keyboard_rows = _build_owner_place_card_action_rows(place_id=place_id, item=item)
    keyboard_rows.append([InlineKeyboardButton(text="« Мої бізнеси", callback_data=CB_MENU_MINE)])
    keyboard_rows.append([InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)])

    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    card_text = await build_business_card_text(item)
    await ui_render(
        callback.message.bot,
        chat_id=callback.message.chat.id,
        prefer_message_id=callback.message.message_id,
        text=card_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_QR_OPEN_PREFIX))
async def cb_open_place_qr(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer()
        return
    payload = callback.data.split(":", 1)
    if len(payload) != 2:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    try:
        place_id = int(payload[1])
    except Exception:
        await callback.answer("Некоректний заклад", show_alert=True)
        return

    user_id = callback.from_user.id
    rows = await cabinet_service.list_user_businesses(user_id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == int(place_id)), None)
    if not item or item.get("ownership_status") != "approved":
        await callback.answer("Доступ лише для підтвердженого власника закладу.", show_alert=True)
        return
    if not _has_active_paid_subscription(item):
        await callback.answer("🔒 QR голосування доступний з активним тарифом Light або вище.", show_alert=True)
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        try:
            await _render_place_plan_menu(
                callback.message,
                tg_user_id=user_id,
                place_id=place_id,
                source="card",
                prefer_message_id=callback.message.message_id,
                notice="🔒 QR голосування доступний з активним тарифом Light або вище.",
            )
        except Exception:
            pass
        return

    deep_link = _resident_place_deeplink(place_id)
    qr_url = _resident_place_qr_url(place_id)
    if not deep_link or not qr_url:
        await callback.answer("Не налаштовано BOT_USERNAME для resident-бота.", show_alert=True)
        return

    place_name = html.escape(str(item.get("place_name") or "вашого закладу"))
    text = (
        f"🔳 <b>QR голосування</b>\n\n"
        f"Заклад: <b>{place_name}</b>\n\n"
        "Розмісти цей QR у закладі, щоб мешканці швидко відкривали картку і ставили ❤️.\n"
        "Лайк у боті фіксується 1 раз на Telegram ID.\n\n"
        f"Deep-link:\n<code>{html.escape(deep_link)}</code>"
    )
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await ui_render(
        callback.message.bot,
        chat_id=callback.message.chat.id,
        prefer_message_id=callback.message.message_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔳 Відкрити QR", url=qr_url)],
                [InlineKeyboardButton(text="🔗 Відкрити deep-link", url=deep_link)],
                [InlineKeyboardButton(text="« До закладу", callback_data=f"{CB_MY_OPEN_PREFIX}{place_id}")],
                [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_QR_KIT_OPEN_PREFIX))
async def cb_open_place_qr_kit(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer()
        return
    payload = callback.data.split(":", 1)
    if len(payload) != 2:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    try:
        place_id = int(payload[1])
    except Exception:
        await callback.answer("Некоректний заклад", show_alert=True)
        return

    user_id = callback.from_user.id
    rows = await cabinet_service.list_user_businesses(user_id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == int(place_id)), None)
    if not item or item.get("ownership_status") != "approved":
        await callback.answer("Доступ лише для підтвердженого власника закладу.", show_alert=True)
        return
    if not _has_active_partner_subscription(item):
        await callback.answer(
            f"🔒 QR-комплект доступний з активним тарифом {PLAN_TITLES['partner']}.",
            show_alert=True,
        )
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        try:
            await _render_place_plan_menu(
                callback.message,
                tg_user_id=user_id,
                place_id=place_id,
                source="card",
                prefer_message_id=callback.message.message_id,
                notice=f"🔒 QR-комплект доступний з активним тарифом {PLAN_TITLES['partner']}.",
            )
        except Exception:
            pass
        return

    deep_link = _resident_place_deeplink(place_id)
    place_name_raw = str(item.get("place_name") or "вашого закладу")
    place_name = html.escape(place_name_raw)
    if not deep_link:
        await callback.answer("Не налаштовано BOT_USERNAME для resident-бота.", show_alert=True)
        return

    entrance_png = _resident_place_qr_kit_png_url(place_id, caption=f"{place_name_raw} • ВХІД")
    cashier_png = _resident_place_qr_kit_png_url(place_id, caption=f"{place_name_raw} • КАСА")
    table_png = _resident_place_qr_kit_png_url(place_id, caption=f"{place_name_raw} • СТОЛИК")
    entrance_pdf = _resident_place_qr_kit_pdf_url(place_id, variant="entrance")
    cashier_pdf = _resident_place_qr_kit_pdf_url(place_id, variant="cashier")
    table_pdf = _resident_place_qr_kit_pdf_url(place_id, variant="table")

    text = (
        f"🪧 <b>QR-комплект</b>\n\n"
        f"Заклад: <b>{place_name}</b>\n\n"
        "Використай готові PNG/PDF-макети для друку (A4) або покажи QR на екрані.\n"
        "Рекомендація: розмісти 2-3 точки (вхід, каса, столики), щоб збільшити охоплення.\n\n"
        "Інструкція:\n"
        "1) Відкрий один із шаблонів нижче (PNG або PDF).\n"
        "2) Надрукуй у форматі A4.\n"
        "3) Розмісти в потрібній зоні.\n\n"
        f"Deep-link:\n<code>{html.escape(deep_link)}</code>"
    )

    rows_kb: list[list[InlineKeyboardButton]] = []
    if entrance_png or entrance_pdf:
        row: list[InlineKeyboardButton] = []
        if entrance_png:
            row.append(InlineKeyboardButton(text="🖼 PNG • Вхід", url=entrance_png))
        if entrance_pdf:
            row.append(InlineKeyboardButton(text="📄 PDF • Вхід", url=entrance_pdf))
        rows_kb.append(row)
    if cashier_png or cashier_pdf:
        row = []
        if cashier_png:
            row.append(InlineKeyboardButton(text="🖼 PNG • Каса", url=cashier_png))
        if cashier_pdf:
            row.append(InlineKeyboardButton(text="📄 PDF • Каса", url=cashier_pdf))
        rows_kb.append(row)
    if table_png or table_pdf:
        row = []
        if table_png:
            row.append(InlineKeyboardButton(text="🖼 PNG • Столик", url=table_png))
        if table_pdf:
            row.append(InlineKeyboardButton(text="📄 PDF • Столик", url=table_pdf))
        rows_kb.append(row)
    rows_kb.extend(
        [
            [InlineKeyboardButton(text="🔗 Відкрити deep-link", url=deep_link)],
            [InlineKeyboardButton(text="« До закладу", callback_data=f"{CB_MY_OPEN_PREFIX}{place_id}")],
            [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
        ]
    )

    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await ui_render(
        callback.message.bot,
        chat_id=callback.message.chat.id,
        prefer_message_id=callback.message.message_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows_kb),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_PARTNER_SUPPORT_PREFIX))
async def cb_partner_support_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    raw = callback.data.removeprefix(CB_PARTNER_SUPPORT_PREFIX)
    try:
        place_id = int(raw)
    except Exception:
        await callback.answer("Некоректний заклад", show_alert=True)
        return

    user_id = callback.from_user.id
    rows = await cabinet_service.list_user_businesses(user_id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == int(place_id)), None)
    if not item or item.get("ownership_status") != "approved":
        await callback.answer("Доступ лише для підтвердженого власника закладу.", show_alert=True)
        return
    if not _has_active_partner_subscription(item):
        await callback.answer(
            f"🔒 Пріоритетна підтримка доступна з активним тарифом {PLAN_TITLES['partner']}.",
            show_alert=True,
        )
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await ui_render(
            callback.message.bot,
            chat_id=callback.message.chat.id,
            prefer_message_id=callback.message.message_id,
            text=f"🔒 Пріоритетна підтримка доступна з активним тарифом {PLAN_TITLES['partner']}.",
            reply_markup=build_plan_select_keyboard(place_id),
            notice=f"🔒 Пріоритетна підтримка доступна з активним тарифом {PLAN_TITLES['partner']}.",
        )
        return

    place_name = html.escape(str(item.get("place_name") or "вашого закладу"))
    await state.set_state(PartnerSupportRequestStates.waiting_text)
    await state.update_data(partner_support_place_id=place_id)
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await ui_render(
        callback.message.bot,
        chat_id=callback.message.chat.id,
        prefer_message_id=callback.message.message_id,
        text=(
            "🧑‍💼 <b>Пріоритетна підтримка (Partner)</b>\n\n"
            f"Заклад: <b>{place_name}</b>\n\n"
            "Опишіть запит або проблему.\n"
            "Ліміт: до 800 символів.\n\n"
            "Адмін отримає пріоритетний запит у бізнес‑панелі."
        ),
        reply_markup=build_partner_support_request_keyboard(place_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_PARTNER_SUPPORT_CANCEL_PREFIX))
async def cb_partner_support_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    raw = callback.data.removeprefix(CB_PARTNER_SUPPORT_CANCEL_PREFIX)
    try:
        place_id = int(raw)
    except Exception:
        await callback.answer("Скасовано")
        await state.clear()
        return

    await state.clear()
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await render_place_card_updated(callback.message, place_id=place_id, note_text="Скасовано.")
    await callback.answer("Скасовано")


@router.callback_query(F.data.startswith(CB_FREE_EDIT_REQUEST_PREFIX))
async def cb_free_edit_request_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    raw = callback.data.removeprefix(CB_FREE_EDIT_REQUEST_PREFIX)
    try:
        place_id = int(raw)
    except Exception:
        await callback.answer("Некоректний заклад", show_alert=True)
        return

    user_id = callback.from_user.id
    rows = await cabinet_service.list_user_businesses(user_id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == int(place_id)), None)
    if not item or item.get("ownership_status") != "approved":
        await callback.answer("Доступ лише для підтвердженого власника закладу.", show_alert=True)
        return
    if _has_active_paid_subscription(item):
        await callback.answer("У вас активний тариф. Редагуйте картку напряму.", show_alert=True)
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await ui_render(
            callback.message.bot,
            chat_id=callback.message.chat.id,
            prefer_message_id=callback.message.message_id,
            text="Що хочеш змінити?",
            reply_markup=build_edit_fields_keyboard(
                place_id,
                has_premium_access=_has_active_premium_subscription(item),
                has_partner_access=_has_active_partner_subscription(item),
            ),
        )
        return

    place_name = html.escape(str(item.get("place_name") or "вашого закладу"))
    await state.set_state(FreeEditRequestStates.waiting_text)
    await state.update_data(free_edit_request_place_id=place_id)
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await ui_render(
        callback.message.bot,
        chat_id=callback.message.chat.id,
        prefer_message_id=callback.message.message_id,
        text=(
            "📝 <b>Запропонувати правку</b>\n\n"
            f"Заклад: <b>{place_name}</b>\n\n"
            "Опишіть, що потрібно виправити в картці закладу.\n"
            "Ліміт: до 600 символів.\n\n"
            "Після цього передамо запит адміну на модерацію."
        ),
        reply_markup=build_free_edit_request_keyboard(place_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_FREE_EDIT_REQUEST_CANCEL_PREFIX))
async def cb_free_edit_request_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    raw = callback.data.removeprefix(CB_FREE_EDIT_REQUEST_CANCEL_PREFIX)
    try:
        place_id = int(raw)
    except Exception:
        await callback.answer("Скасовано", show_alert=False)
        await state.clear()
        return

    await state.clear()
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await render_place_card_updated(callback.message, place_id=place_id, note_text="Скасовано.")
    await callback.answer("Скасовано")


@router.message(FreeEditRequestStates.waiting_text, F.text)
async def msg_free_edit_request_submit(message: Message, state: FSMContext) -> None:
    await try_delete_user_message(message)
    data = await state.get_data()
    place_id = int(data.get("free_edit_request_place_id") or 0)
    if place_id <= 0:
        await state.clear()
        await send_main_menu(message, message.chat.id)
        return

    raw = str(message.text or "").strip()
    if not raw:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text="❌ Порожній текст. Опишіть, що потрібно виправити.",
            reply_markup=build_free_edit_request_keyboard(place_id),
        )
        return
    if len(raw) > 600:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text="❌ Занадто довгий текст. Максимум 600 символів.",
            reply_markup=build_free_edit_request_keyboard(place_id),
        )
        return

    user_id = message.from_user.id if message.from_user else message.chat.id
    rows = await cabinet_service.list_user_businesses(user_id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == int(place_id)), None)
    if not item or item.get("ownership_status") != "approved":
        await state.clear()
        await send_main_menu(message, user_id)
        return
    if _has_active_paid_subscription(item):
        await state.clear()
        await render_place_card_updated(
            message,
            place_id=place_id,
            note_text="У вас активний тариф Light або вище. Редагуйте картку напряму.",
        )
        return

    from_user = message.from_user
    report = await create_place_report(
        place_id=place_id,
        reporter_tg_user_id=int(from_user.id if from_user else message.chat.id),
        reporter_username=str(from_user.username or "") if from_user else "",
        reporter_first_name=str(from_user.first_name or "") if from_user else "",
        reporter_last_name=str(from_user.last_name or "") if from_user else "",
        report_text=raw,
    )
    if not report:
        await state.clear()
        await render_place_card_updated(
            message,
            place_id=place_id,
            note_text="❌ Не вдалося створити запит. Спробуйте ще раз пізніше.",
        )
        return

    place = await cabinet_service.repository.get_place(place_id)
    place_name = str((place or {}).get("name") or f"ID {place_id}")
    payload = {
        "report_id": int(report["id"]),
        "place_id": int(place_id),
        "place_name": place_name,
        "reporter_tg_user_id": int(from_user.id if from_user else message.chat.id),
        "reporter_username": str(from_user.username or "") if from_user else "",
        "reporter_first_name": str(from_user.first_name or "") if from_user else "",
        "reporter_last_name": str(from_user.last_name or "") if from_user else "",
        "report_text": raw,
        "created_at": str(report.get("created_at") or ""),
        "source": "business_owner_free_edit",
    }
    try:
        await create_admin_job(
            "admin_place_report_alert",
            payload,
            created_by=int(from_user.id if from_user else message.chat.id),
        )
    except Exception:
        logger.exception("Failed to enqueue admin_place_report_alert report_id=%s", report.get("id"))

    await state.clear()
    await render_place_card_updated(
        message,
        place_id=place_id,
        note_text="✅ Передали правку адміну на модерацію.",
    )


@router.message(FreeEditRequestStates.waiting_text)
async def msg_free_edit_request_non_text(message: Message, state: FSMContext) -> None:
    await try_delete_user_message(message)
    data = await state.get_data()
    place_id = int(data.get("free_edit_request_place_id") or 0)
    reply_markup = build_free_edit_request_keyboard(place_id) if place_id > 0 else build_cancel_menu()
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        text="📝 Надішліть текст правки (до 600 символів).",
        reply_markup=reply_markup,
    )


@router.message(PartnerSupportRequestStates.waiting_text, F.text)
async def msg_partner_support_submit(message: Message, state: FSMContext) -> None:
    await try_delete_user_message(message)
    data = await state.get_data()
    place_id = int(data.get("partner_support_place_id") or 0)
    if place_id <= 0:
        await state.clear()
        await send_main_menu(message, message.chat.id)
        return

    raw = str(message.text or "").strip()
    if not raw:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text="❌ Порожній текст. Опишіть запит до підтримки.",
            reply_markup=build_partner_support_request_keyboard(place_id),
        )
        return
    if len(raw) > 800:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text="❌ Занадто довгий текст. Максимум 800 символів.",
            reply_markup=build_partner_support_request_keyboard(place_id),
        )
        return

    user_id = message.from_user.id if message.from_user else message.chat.id
    rows = await cabinet_service.list_user_businesses(user_id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == int(place_id)), None)
    if not item or item.get("ownership_status") != "approved":
        await state.clear()
        await send_main_menu(message, user_id)
        return
    if not _has_active_partner_subscription(item):
        await state.clear()
        await render_place_card_updated(
            message,
            place_id=place_id,
            note_text=f"🔒 Пріоритетна підтримка доступна з активним тарифом {PLAN_TITLES['partner']}.",
        )
        return

    from_user = message.from_user
    support_request = await create_business_support_request(
        place_id=place_id,
        owner_tg_user_id=int(from_user.id if from_user else message.chat.id),
        owner_username=str(from_user.username or "") if from_user else "",
        owner_first_name=str(from_user.first_name or "") if from_user else "",
        owner_last_name=str(from_user.last_name or "") if from_user else "",
        message_text=raw,
    )
    if not support_request:
        await state.clear()
        await render_place_card_updated(
            message,
            place_id=place_id,
            note_text="❌ Не вдалося створити запит. Спробуйте ще раз пізніше.",
        )
        return

    place = await cabinet_service.repository.get_place(place_id)
    place_name = str((place or {}).get("name") or f"ID {place_id}")
    payload = {
        "support_request_id": int(support_request["id"]),
        "place_id": int(place_id),
        "place_name": place_name,
        "owner_tg_user_id": int(from_user.id if from_user else message.chat.id),
        "owner_username": str(from_user.username or "") if from_user else "",
        "owner_first_name": str(from_user.first_name or "") if from_user else "",
        "owner_last_name": str(from_user.last_name or "") if from_user else "",
        "message_text": raw,
        "created_at": str(support_request.get("created_at") or ""),
        "source": "business_partner_priority_support",
    }
    try:
        await create_admin_job(
            "admin_partner_support_alert",
            payload,
            created_by=int(from_user.id if from_user else message.chat.id),
        )
    except Exception:
        logger.exception("Failed to enqueue admin_partner_support_alert request_id=%s", support_request.get("id"))

    await state.clear()
    await render_place_card_updated(
        message,
        place_id=place_id,
        note_text="✅ Запит у пріоритетну підтримку передано адміну.",
    )


@router.message(PartnerSupportRequestStates.waiting_text)
async def msg_partner_support_non_text(message: Message, state: FSMContext) -> None:
    await try_delete_user_message(message)
    data = await state.get_data()
    place_id = int(data.get("partner_support_place_id") or 0)
    reply_markup = build_partner_support_request_keyboard(place_id) if place_id > 0 else build_cancel_menu()
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        text="🧑‍💼 Надішліть текст запиту до пріоритетної підтримки (до 800 символів).",
        reply_markup=reply_markup,
    )


@router.callback_query(F.data.startswith("be:"))
async def cb_edit_place(callback: CallbackQuery) -> None:
    payload = callback.data.split(":")
    if len(payload) != 2:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    place_id = int(payload[1])
    user_id = callback.from_user.id
    rows = await cabinet_service.list_user_businesses(user_id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == int(place_id)), None)
    if not item or item.get("ownership_status") != "approved":
        await callback.answer("Доступ лише для підтвердженого власника закладу.", show_alert=True)
        return
    if not _has_active_paid_subscription(item):
        await callback.answer("🔒 Редагування доступне з активним тарифом Light або вище.", show_alert=True)
        if callback.message:
            await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
            try:
                await _render_place_plan_menu(
                    callback.message,
                    tg_user_id=user_id,
                    place_id=place_id,
                    source="card",
                    prefer_message_id=callback.message.message_id,
                    notice="🔒 Редагування картки доступне з активним тарифом Light або вище.",
                )
            except Exception:
                pass
        return
    if callback.message:
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await ui_render(
            callback.message.bot,
            chat_id=callback.message.chat.id,
            prefer_message_id=callback.message.message_id,
            text="Що хочеш змінити?",
            reply_markup=build_edit_fields_keyboard(
                place_id,
                has_premium_access=_has_active_premium_subscription(item),
                has_partner_access=_has_active_partner_subscription(item),
            ),
        )
    await callback.answer()


async def render_gallery_menu(
    message: Message,
    *,
    tg_user_id: int,
    place_id: int,
    prefer_message_id: int | None = None,
    notice_text: str | None = None,
) -> None:
    try:
        rows = await cabinet_service.list_user_businesses(tg_user_id)
        item = next((row for row in rows if int(row.get("place_id") or 0) == int(place_id)), None)
        place_name = str(item.get("place_name") or f"ID {place_id}") if item else f"ID {place_id}"
        gallery_items = await cabinet_service.list_place_gallery_media(
            tg_user_id=tg_user_id,
            place_id=place_id,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            prefer_message_id=prefer_message_id,
            text=str(error),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="« Назад", callback_data=f"be:{int(place_id)}")],
                    [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
                ]
            ),
        )
        return

    total = len(gallery_items)
    lines = [
        "🖼 <b>Галерея закладу</b>",
        "",
        f"🏢 {html.escape(place_name)}",
        f"📸 Додано фото: <b>{total}</b>",
    ]
    if gallery_items:
        lines.append("")
        lines.append("Поточні елементи:")
        for idx, media in enumerate(gallery_items[:8], start=1):
            lines.append(f"• Фото {idx}: <code>{html.escape(_truncate_media_ref(str(media.get('media_ref') or '')))}</code>")
        if total > 8:
            lines.append(f"… і ще {total - 8}")
    if notice_text:
        lines.append("")
        lines.append(html.escape(str(notice_text)))

    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        prefer_message_id=prefer_message_id,
        text="\n".join(lines),
        reply_markup=build_gallery_manage_keyboard(place_id, gallery_items),
    )


@router.callback_query(F.data.startswith(CB_GALLERY_MENU_PREFIX))
async def cb_gallery_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    raw = callback.data.removeprefix(CB_GALLERY_MENU_PREFIX)
    try:
        place_id = int(raw)
    except Exception:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    await state.clear()
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await render_gallery_menu(
        callback.message,
        tg_user_id=callback.from_user.id,
        place_id=place_id,
        prefer_message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_GALLERY_ADD_PREFIX))
async def cb_gallery_add(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    raw = callback.data.removeprefix(CB_GALLERY_ADD_PREFIX)
    try:
        place_id = int(raw)
    except Exception:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    await state.set_state(EditPlaceStates.waiting_gallery_media)
    await state.update_data(place_id=place_id)
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await ui_render(
        callback.message.bot,
        chat_id=callback.message.chat.id,
        prefer_message_id=callback.message.message_id,
        text=(
            "Надішли фото, URL або Telegram <code>file_id</code>.\n\n"
            "Ліміти: Light до 6 фото, Premium до 12, Partner до 20."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_MENU_CANCEL)],
                [InlineKeyboardButton(text="« До галереї", callback_data=f"{CB_GALLERY_MENU_PREFIX}{place_id}")],
                [InlineKeyboardButton(text="« Меню", callback_data=CB_MENU_HOME)],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_GALLERY_DEL_PREFIX))
async def cb_gallery_delete(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    raw = callback.data.removeprefix(CB_GALLERY_DEL_PREFIX)
    parts = raw.split(":", 1)
    if len(parts) != 2:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    try:
        place_id = int(parts[0])
        media_id = int(parts[1])
    except Exception:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    try:
        await cabinet_service.remove_place_gallery_media(
            tg_user_id=callback.from_user.id,
            place_id=place_id,
            media_id=media_id,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return

    await state.clear()
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await render_gallery_menu(
        callback.message,
        tg_user_id=callback.from_user.id,
        place_id=place_id,
        prefer_message_id=callback.message.message_id,
        notice_text="✅ Фото видалено.",
    )
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("bef:"))
async def cb_edit_field_pick(callback: CallbackQuery, state: FSMContext) -> None:
    payload = callback.data.split(":")
    if len(payload) != 3:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    place_id = int(payload[1])
    field = payload[2]
    rows = await cabinet_service.list_user_businesses(callback.from_user.id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == int(place_id)), None)
    if not item or item.get("ownership_status") != "approved":
        await callback.answer("Доступ лише для підтвердженого власника закладу.", show_alert=True)
        await state.clear()
        return
    if not _has_active_paid_subscription(item):
        await callback.answer("🔒 Редагування доступне з активним тарифом Light або вище.", show_alert=True)
        await state.clear()
        if callback.message:
            await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
            try:
                await _render_place_plan_menu(
                    callback.message,
                    tg_user_id=callback.from_user.id,
                    place_id=place_id,
                    source="card",
                    prefer_message_id=callback.message.message_id,
                    notice="🔒 Редагування картки доступне з активним тарифом Light або вище.",
                )
            except Exception:
                pass
        return
    if field in {
        "menu_url",
        "order_url",
        "offer_1_text",
        "offer_2_text",
        "offer_1_image_url",
        "offer_2_image_url",
    } and not _has_active_premium_subscription(item):
        await callback.answer("🔒 Ця опція доступна з активним Premium або Partner.", show_alert=True)
        await state.clear()
        if callback.message:
            await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
            try:
                await _render_place_plan_menu(
                    callback.message,
                    tg_user_id=callback.from_user.id,
                    place_id=place_id,
                    source="card",
                    prefer_message_id=callback.message.message_id,
                    notice="🔒 Premium-функції (меню/замовлення/офери/фото) доступні з Premium або Partner.",
                )
            except Exception:
                pass
        return
    if field in {"photo_1_url", "photo_2_url", "photo_3_url"} and not _has_active_partner_subscription(item):
        await callback.answer("🔒 Ця опція доступна з активним Partner.", show_alert=True)
        await state.clear()
        if callback.message:
            await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
            try:
                await _render_place_plan_menu(
                    callback.message,
                    tg_user_id=callback.from_user.id,
                    place_id=place_id,
                    source="card",
                    prefer_message_id=callback.message.message_id,
                    notice="🔒 Брендована галерея доступна з активним Partner.",
                )
            except Exception:
                pass
        return
    if field == "address":
        await state.set_state(EditPlaceStates.waiting_address_building)
        await state.update_data(place_id=place_id, field=field)
        if callback.message:
            await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
            await send_edit_building_picker(
                callback.message,
                state,
                place_id=place_id,
                prefer_message_id=callback.message.message_id,
            )
        await callback.answer()
        return
    if field == "contact":
        await state.set_state(EditPlaceStates.waiting_contact_type)
        await state.update_data(place_id=place_id, field=field)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📞 Подзвонити",
                        callback_data=f"{CB_CONTACT_PICK_PREFIX}{place_id}:call",
                    ),
                    InlineKeyboardButton(
                        text="💬 Написати",
                        callback_data=f"{CB_CONTACT_PICK_PREFIX}{place_id}:chat",
                    ),
                ],
                [InlineKeyboardButton(text="❌ Прибрати контакт", callback_data=f"{CB_CONTACT_CLEAR_PREFIX}{place_id}")],
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
                text="Оберіть тип контактної кнопки:",
                reply_markup=keyboard,
            )
        await callback.answer()
        return

    field_label = {
        "name": "назву",
        "description": "опис",
        "address": "адресу",
        "opening_hours": "години роботи",
        "link_url": "посилання",
        "logo_url": "URL або file_id логотипу/фото",
        "promo_code": "промокод",
        "menu_url": "посилання на меню/прайс",
        "order_url": "посилання на замовлення/запис",
        "offer_1_text": "текст оферу №1",
        "offer_2_text": "текст оферу №2",
        "offer_1_image_url": "URL або file_id фото оферу №1",
        "offer_2_image_url": "URL або file_id фото оферу №2",
        "photo_1_url": "URL або file_id фото №1",
        "photo_2_url": "URL або file_id фото №2",
        "photo_3_url": "URL або file_id фото №3",
    }.get(field, field)
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
        extra_note = ""
        if field in BUSINESS_PROFILE_FIELDS:
            extra_note = "\n\nНадішли <code>-</code>, щоб прибрати це поле."
        if field in MEDIA_PROFILE_FIELDS:
            extra_note += "\n\nДля медіа можна надіслати URL, Telegram <code>file_id</code> або просто фото повідомленням."
        if field == "promo_code":
            extra_note += "\n\nФормат: 2-32 символи, латиниця/цифри, також дозволено <code>-</code> та <code>_</code>."
        await ui_render(
            callback.message.bot,
            chat_id=callback.message.chat.id,
            prefer_message_id=callback.message.message_id,
            text=f"Надішли нову {field_label}.{extra_note}",
            reply_markup=keyboard,
        )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_EDIT_BUILDING_PICK_PREFIX))
async def cb_edit_building_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != EditPlaceStates.waiting_address_building.state:
        await callback.answer("Це меню вже неактуальне. Натисни /start.", show_alert=True)
        return
    raw = callback.data.removeprefix(CB_EDIT_BUILDING_PICK_PREFIX)
    parts = raw.split(":")
    if len(parts) != 2:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    try:
        place_id = int(parts[0])
        building_id = int(parts[1])
    except Exception:
        await callback.answer("Некоректні дані", show_alert=True)
        return

    rows = await cabinet_service.list_user_businesses(callback.from_user.id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == int(place_id)), None)
    if not item or item.get("ownership_status") != "approved":
        await callback.answer("Доступ лише для підтвердженого власника закладу.", show_alert=True)
        await state.clear()
        return
    if not _has_active_paid_subscription(item):
        await callback.answer("🔒 Редагування доступне з активним тарифом Light або вище.", show_alert=True)
        await state.clear()
        if callback.message:
            await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
            try:
                await _render_place_plan_menu(
                    callback.message,
                    tg_user_id=callback.from_user.id,
                    place_id=place_id,
                    source="card",
                    prefer_message_id=callback.message.message_id,
                    notice="🔒 Редагування картки доступне з активним тарифом Light або вище.",
                )
            except Exception:
                pass
        return

    building = await cabinet_service.repository.get_building(building_id)
    if not building:
        await callback.answer("Будинок не знайдено", show_alert=True)
        return

    building_label = _format_building_display(building)
    await state.update_data(place_id=place_id, field="address", building_id=building_id, building_label=building_label)
    await state.set_state(EditPlaceStates.waiting_address_details)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_MENU_CANCEL)],
            [InlineKeyboardButton(text="⬅️ Змінити будинок", callback_data=f"{CB_EDIT_BUILDING_CHANGE_PREFIX}{place_id}")],
            [InlineKeyboardButton(text="« Назад", callback_data=f"be:{place_id}")],
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


@router.callback_query(F.data.startswith(CB_EDIT_BUILDING_CHANGE_PREFIX))
async def cb_edit_building_change(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    raw = callback.data.removeprefix(CB_EDIT_BUILDING_CHANGE_PREFIX)
    try:
        place_id = int(raw)
    except Exception:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    rows = await cabinet_service.list_user_businesses(callback.from_user.id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == int(place_id)), None)
    if not item or item.get("ownership_status") != "approved":
        await callback.answer("Доступ лише для підтвердженого власника закладу.", show_alert=True)
        await state.clear()
        return
    if not _has_active_paid_subscription(item):
        await callback.answer("🔒 Редагування доступне з активним тарифом Light або вище.", show_alert=True)
        await state.clear()
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        try:
            await _render_place_plan_menu(
                callback.message,
                tg_user_id=callback.from_user.id,
                place_id=place_id,
                source="card",
                prefer_message_id=callback.message.message_id,
                notice="🔒 Редагування картки доступне з активним тарифом Light або вище.",
            )
        except Exception:
            pass
        return
    await state.set_state(EditPlaceStates.waiting_address_building)
    await state.update_data(place_id=place_id, field="address")
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await send_edit_building_picker(
        callback.message,
        state,
        place_id=place_id,
        prefer_message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_CONTACT_PICK_PREFIX))
async def cb_edit_contact_type_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    if await state.get_state() != EditPlaceStates.waiting_contact_type.state:
        await callback.answer("Це меню вже неактуальне. Натисни /start.", show_alert=True)
        return
    raw = callback.data.removeprefix(CB_CONTACT_PICK_PREFIX)
    parts = raw.split(":", 1)
    if len(parts) != 2:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    try:
        place_id = int(parts[0])
    except Exception:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    ctype = str(parts[1] or "").strip().lower()
    if ctype not in {"call", "chat"}:
        await callback.answer("Некоректний тип контакту", show_alert=True)
        return

    rows = await cabinet_service.list_user_businesses(callback.from_user.id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == int(place_id)), None)
    if not item or item.get("ownership_status") != "approved":
        await callback.answer("Недостатньо прав.", show_alert=True)
        await state.clear()
        return
    if not _has_active_paid_subscription(item):
        await callback.answer("🔒 Доступно з активним тарифом Light або вище.", show_alert=True)
        await state.clear()
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        try:
            await _render_place_plan_menu(
                callback.message,
                tg_user_id=callback.from_user.id,
                place_id=place_id,
                source="card",
                prefer_message_id=callback.message.message_id,
                notice="🔒 Контактні кнопки доступні з активним тарифом Light або вище.",
            )
        except Exception:
            pass
        return

    await state.set_state(EditPlaceStates.waiting_contact_value)
    await state.update_data(place_id=place_id, field="contact", contact_type=ctype)

    prompt = (
        "Надішли номер телефону (наприклад <code>+380671234567</code>)"
        if ctype == "call"
        else "Надішли @username або посилання на Telegram (t.me/...)"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_MENU_CANCEL)],
            [InlineKeyboardButton(text="« Назад", callback_data=f"be:{place_id}")],
        ]
    )
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await ui_render(
        callback.message.bot,
        chat_id=callback.message.chat.id,
        prefer_message_id=callback.message.message_id,
        text=f"{prompt}\n\nНадішли <code>-</code>, щоб прибрати контакт.",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB_CONTACT_CLEAR_PREFIX))
async def cb_edit_contact_clear(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    raw = callback.data.removeprefix(CB_CONTACT_CLEAR_PREFIX)
    try:
        place_id = int(raw)
    except Exception:
        await callback.answer("Некоректні дані", show_alert=True)
        return

    rows = await cabinet_service.list_user_businesses(callback.from_user.id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == int(place_id)), None)
    if not item or item.get("ownership_status") != "approved":
        await callback.answer("Недостатньо прав.", show_alert=True)
        await state.clear()
        return
    if not _has_active_paid_subscription(item):
        await callback.answer("🔒 Доступно з активним тарифом Light або вище.", show_alert=True)
        await state.clear()
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        try:
            await _render_place_plan_menu(
                callback.message,
                tg_user_id=callback.from_user.id,
                place_id=place_id,
                source="card",
                prefer_message_id=callback.message.message_id,
                notice="🔒 Контактні кнопки доступні з активним тарифом Light або вище.",
            )
        except Exception:
            pass
        return

    try:
        await cabinet_service.update_place_contact(
            callback.from_user.id,
            place_id=place_id,
            contact_type=None,
            contact_value=None,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return

    await state.clear()
    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await render_place_card_updated(callback.message, place_id=place_id, note_text="✅ Контакт прибрано.")
    await callback.answer("Готово")


@router.message(EditPlaceStates.waiting_contact_value, F.text)
async def edit_place_contact_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    place_id = int(data.get("place_id") or 0)
    ctype = str(data.get("contact_type") or "").strip().lower()
    value = str(message.text or "").strip()
    await try_delete_user_message(message)
    if place_id <= 0:
        await state.clear()
        await send_main_menu(message, message.chat.id)
        return
    try:
        await cabinet_service.update_place_contact(
            tg_user_id=message.from_user.id if message.from_user else message.chat.id,
            place_id=place_id,
            contact_type=ctype,
            contact_value=value,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_MENU_CANCEL)],
                [InlineKeyboardButton(text="« Назад", callback_data=f"be:{place_id}")],
            ]
        )
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text=str(error),
            reply_markup=keyboard,
        )
        return

    await state.clear()
    await render_place_card_updated(message, place_id=place_id, note_text="✅ Контакт оновлено.")


@router.message(EditPlaceStates.waiting_address_building, F.text)
async def edit_place_address_building_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    place_id = int(data.get("place_id") or 0)
    await try_delete_user_message(message)
    if place_id <= 0:
        await state.clear()
        await send_main_menu(message, message.chat.id)
        return
    await send_edit_building_picker(message, state, place_id=place_id)


@router.message(EditPlaceStates.waiting_address_details, F.text)
async def edit_place_address_details(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    place_id = int(data.get("place_id") or 0)
    details = message.text.strip()
    if details == "-":
        details = ""

    building_label = str(data.get("building_label") or "").strip()
    if not building_label:
        await state.set_state(EditPlaceStates.waiting_address_building)
        await send_edit_building_picker(message, state, place_id=place_id)
        return

    address = building_label
    if details:
        address = f"{building_label}, {details}".strip(", ")

    try:
        await cabinet_service.update_place_field(
            tg_user_id=message.from_user.id if message.from_user else message.chat.id,
            place_id=place_id,
            field="address",
            value=address,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        if isinstance(error, AccessDeniedError):
            await try_delete_user_message(message)
            await state.clear()
            try:
                await _render_place_plan_menu(
                    message,
                    tg_user_id=message.from_user.id if message.from_user else message.chat.id,
                    place_id=place_id,
                    source="card",
                    notice="🔒 Редагування картки доступне з активним тарифом Light або вище.",
                )
            except Exception:
                pass
            return
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_MENU_CANCEL)],
                [InlineKeyboardButton(text="⬅️ Змінити будинок", callback_data=f"{CB_EDIT_BUILDING_CHANGE_PREFIX}{place_id}")],
                [InlineKeyboardButton(text="« Назад", callback_data=f"be:{place_id}")],
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
    await render_place_card_updated(message, place_id=place_id, note_text="✅ Картку оновлено.")


@router.message(EditPlaceStates.waiting_value, F.text)
async def edit_place_apply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    place_id = int(data["place_id"])
    field = str(data["field"])
    try:
        if field in BUSINESS_PROFILE_FIELDS:
            updated_place = await cabinet_service.update_place_business_profile_field(
                tg_user_id=message.from_user.id if message.from_user else message.chat.id,
                place_id=place_id,
                field=field,
                value=message.text,
            )
        else:
            updated_place = await cabinet_service.update_place_field(
                tg_user_id=message.from_user.id if message.from_user else message.chat.id,
                place_id=place_id,
                field=field,
                value=message.text,
            )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        if isinstance(error, AccessDeniedError):
            await try_delete_user_message(message)
            await state.clear()
            try:
                await _render_place_plan_menu(
                    message,
                    tg_user_id=message.from_user.id if message.from_user else message.chat.id,
                    place_id=place_id,
                    source="card",
                    notice="🔒 Редагування картки доступне з активним тарифом Light або вище.",
                )
            except Exception:
                pass
            return
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text=str(error),
            reply_markup=build_cancel_menu(),
        )
        return
    await try_delete_user_message(message)
    await state.clear()
    await render_place_card_updated(message, place_id=place_id, note_text="✅ Картку оновлено.")


@router.message(EditPlaceStates.waiting_value, F.photo)
async def edit_place_apply_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    place_id = int(data["place_id"])
    field = str(data["field"])
    if field not in MEDIA_PROFILE_FIELDS:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text="Для цього поля надішли текстове значення.",
            reply_markup=build_cancel_menu(),
        )
        return
    if not message.photo:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text="Не вдалося прочитати фото. Спробуй ще раз.",
            reply_markup=build_cancel_menu(),
        )
        return

    file_id = str(message.photo[-1].file_id or "").strip()
    if not file_id:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text="Не вдалося отримати file_id для фото. Спробуй ще раз.",
            reply_markup=build_cancel_menu(),
        )
        return

    try:
        await cabinet_service.update_place_business_profile_field(
            tg_user_id=message.from_user.id if message.from_user else message.chat.id,
            place_id=place_id,
            field=field,
            value=file_id,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        if isinstance(error, AccessDeniedError):
            await try_delete_user_message(message)
            await state.clear()
            try:
                await _render_place_plan_menu(
                    message,
                    tg_user_id=message.from_user.id if message.from_user else message.chat.id,
                    place_id=place_id,
                    source="card",
                    notice="🔒 Редагування картки доступне з активним тарифом Light або вище.",
                )
            except Exception:
                pass
            return
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text=str(error),
            reply_markup=build_cancel_menu(),
        )
        return

    await try_delete_user_message(message)
    await state.clear()
    await render_place_card_updated(message, place_id=place_id, note_text="✅ Фото оновлено.")


@router.message(EditPlaceStates.waiting_gallery_media, F.photo)
async def edit_place_gallery_add_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    place_id = int(data.get("place_id") or 0)
    if place_id <= 0:
        await state.clear()
        await send_main_menu(message, message.chat.id)
        return
    if not message.photo:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text="Не вдалося прочитати фото. Спробуй ще раз.",
            reply_markup=build_cancel_menu(),
        )
        return
    file_id = str(message.photo[-1].file_id or "").strip()
    if not file_id:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text="Не вдалося отримати file_id для фото. Спробуй ще раз.",
            reply_markup=build_cancel_menu(),
        )
        return
    try:
        await cabinet_service.add_place_gallery_media(
            tg_user_id=message.from_user.id if message.from_user else message.chat.id,
            place_id=place_id,
            media_ref=file_id,
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
    await render_gallery_menu(
        message,
        tg_user_id=message.from_user.id if message.from_user else message.chat.id,
        place_id=place_id,
        notice_text="✅ Фото додано в галерею.",
    )


@router.message(EditPlaceStates.waiting_gallery_media, F.text)
async def edit_place_gallery_add_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    place_id = int(data.get("place_id") or 0)
    if place_id <= 0:
        await state.clear()
        await send_main_menu(message, message.chat.id)
        return
    value = str(message.text or "").strip()
    if value == "-":
        await try_delete_user_message(message)
        await state.clear()
        await render_gallery_menu(
            message,
            tg_user_id=message.from_user.id if message.from_user else message.chat.id,
            place_id=place_id,
            notice_text="Скасовано.",
        )
        return
    try:
        await cabinet_service.add_place_gallery_media(
            tg_user_id=message.from_user.id if message.from_user else message.chat.id,
            place_id=place_id,
            media_ref=value,
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
    await render_gallery_menu(
        message,
        tg_user_id=message.from_user.id if message.from_user else message.chat.id,
        place_id=place_id,
        notice_text="✅ Фото додано в галерею.",
    )


@router.message(EditPlaceStates.waiting_gallery_media)
async def edit_place_gallery_add_invalid(message: Message) -> None:
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        text="Надішли фото, URL або Telegram file_id.",
        reply_markup=build_cancel_menu(),
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


async def _render_place_plan_menu(
    message: Message,
    *,
    tg_user_id: int,
    place_id: int,
    source: str,
    prefer_message_id: int | None = None,
    notice: str | None = None,
) -> None:
    rows = await cabinet_service.list_user_businesses(tg_user_id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == int(place_id)), None)
    if not item or item.get("ownership_status") != "approved":
        raise AccessDeniedError("Недостатньо прав.")

    back_cb = CB_MENU_PLANS if source == "plans" else f"{CB_MY_OPEN_PREFIX}{place_id}"
    place_name = html.escape(str(item.get("place_name") or "вашого закладу"))
    extra_block = ""
    tier_now = str(item.get("tier") or "").strip().lower()
    sub_status_now = str(item.get("subscription_status") or "").strip().lower()
    sub_expires_raw = str(item.get("subscription_expires_at") or "").strip() or None
    sub_expires_dt = _parse_iso_utc(sub_expires_raw)
    if (
        tier_now in PAID_TIERS
        and sub_status_now == "canceled"
        and sub_expires_dt
        and sub_expires_dt > datetime.now(timezone.utc)
    ):
        extra_block += (
            "\n\n"
            f"🔴 Автопродовження скасовано. Тариф діє до: <b>{html.escape(_format_expires_short(sub_expires_raw))}</b>."
        )

    if str(item.get("tier") or "").strip().lower() == "free":
        try:
            motivation = await cabinet_service.get_free_tier_click_motivation(tg_user_id, place_id)
        except Exception:
            motivation = None
            logger.exception("Failed to load free-tier click motivation place_id=%s", place_id)
        if motivation:
            days = int(motivation.get("days") or 30)
            service_name = html.escape(str(motivation.get("service_name") or "вашій категорії"))
            total_views = int(motivation.get("total_views") or 0)
            own = int(motivation.get("own_views") or 0)
            own_rank = int(motivation.get("own_rank") or 0)
            place_count = int(motivation.get("place_count") or 0)
            top_bucket_size = int(motivation.get("top_bucket_size") or 3)
            top_bucket_views = int(motivation.get("top_bucket_views") or 0)
            others_views = int(motivation.get("others_views") or 0)
            top_share_pct = int(motivation.get("top_share_pct") or 0)
            others_share_pct = int(motivation.get("others_share_pct") or 0)
            own_in_top_bucket = bool(motivation.get("own_in_top_bucket"))
            cta_line = (
                "🚀 Ви вже в топі. Тариф Light допоможе закріпитись вище (✅ Verified + пріоритет)."
                if own_in_top_bucket
                else "🚀 Щоб піднятись у топ, просіть гостей ставити ❤️ у боті або підключіть Light (✅ Verified + пріоритет)."
            )
            extra_block = (
                "\n\n"
                f"📊 <b>Попит у категорії за {days} днів</b>\n"
                f"У категорії «{service_name}» картки відкривали <b>{total_views}</b> разів.\n"
                f"🥇 Топ-{top_bucket_size}: <b>{top_bucket_views}</b> ({top_share_pct}%)\n"
                f"📍 Інші заклади: <b>{others_views}</b> ({others_share_pct}%)\n"
                f"🏪 Ваш заклад: <b>{own}</b> переглядів • місце <b>#{own_rank}</b> з {place_count}\n"
                f"{cta_line}"
            )

    header = f"{notice}\n\n" if notice else ""
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        prefer_message_id=prefer_message_id,
        text=f"{header}Обери тариф для <b>{place_name}</b>:{extra_block}",
        reply_markup=build_plan_keyboard(
            place_id,
            item["tier"],
            current_status=str(item.get("subscription_status") or "inactive"),
            current_expires_at=str(item.get("subscription_expires_at") or "").strip() or None,
            back_callback_data=back_cb,
            source=source,
        ),
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
    if callback.message:
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        try:
            await _render_place_plan_menu(
                callback.message,
                tg_user_id=callback.from_user.id,
                place_id=place_id,
                source=source,
                prefer_message_id=callback.message.message_id,
            )
        except AccessDeniedError as error:
            await callback.answer(str(error), show_alert=True)
            return
    await callback.answer()


@router.callback_query(F.data.startswith("bp_cancel:"))
async def cb_cancel_auto_renew(callback: CallbackQuery) -> None:
    payload = callback.data.split(":")
    if len(payload) not in (2, 3):
        await callback.answer("Некоректні дані", show_alert=True)
        return
    try:
        place_id = int(payload[1])
    except Exception:
        await callback.answer("Некоректний заклад", show_alert=True)
        return
    source = payload[2] if len(payload) == 3 else "card"

    try:
        subscription = await cabinet_service.cancel_subscription_auto_renew(
            tg_user_id=callback.from_user.id,
            place_id=place_id,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        if callback.message:
            await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
            try:
                await _render_place_plan_menu(
                    callback.message,
                    tg_user_id=callback.from_user.id,
                    place_id=place_id,
                    source=source,
                    prefer_message_id=callback.message.message_id,
                    notice=str(error),
                )
            except Exception:
                pass
        await callback.answer(str(error), show_alert=True)
        return

    if callback.message:
        expires_label = _format_expires_short(str(subscription.get("expires_at") or "").strip() or None)
        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await _render_place_plan_menu(
            callback.message,
            tg_user_id=callback.from_user.id,
            place_id=place_id,
            source=source,
            prefer_message_id=callback.message.message_id,
            notice=f"✅ Автопродовження скасовано.\nТариф діє до: <b>{html.escape(expires_label)}</b>.",
        )
    await callback.answer("Автопродовження скасовано")


@router.callback_query(F.data.startswith("bp:"))
async def cb_change_plan(callback: CallbackQuery) -> None:
    payload = callback.data.split(":")
    if len(payload) not in (3, 4):
        await callback.answer("Некоректні дані", show_alert=True)
        return
    place_id = int(payload[1])
    tier = payload[2]
    source = payload[3] if len(payload) == 4 else "card"
    normalized_tier = str(tier).strip().lower()

    # Free plan does not require payment.
    if normalized_tier == "free":
        try:
            subscription = await cabinet_service.change_subscription_tier(
                tg_user_id=callback.from_user.id,
                place_id=place_id,
                tier=tier,
            )
        except (ValidationError, NotFoundError, AccessDeniedError) as error:
            if callback.message and isinstance(error, ValidationError):
                await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
                try:
                    await _render_place_plan_menu(
                        callback.message,
                        tg_user_id=callback.from_user.id,
                        place_id=place_id,
                        source=source,
                        prefer_message_id=callback.message.message_id,
                        notice=str(error),
                    )
                except Exception:
                    pass
            await callback.answer(str(error), show_alert=True)
            return
        if not callback.message:
            await callback.answer("Тариф оновлено")
            return

        rows = await cabinet_service.list_user_businesses(callback.from_user.id)
        item = next((row for row in rows if int(row.get("place_id") or 0) == place_id), None)
        place_name = html.escape(str(item.get("place_name") if item else "вашого закладу"))
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
                current_status=str(subscription.get("status") or "inactive"),
                current_expires_at=str(subscription.get("expires_at") or "").strip() or None,
                back_callback_data=back_cb,
                source=source,
            ),
        )
        await callback.answer("Тариф оновлено")
        return

    try:
        intent = await cabinet_service.create_payment_intent(
            tg_user_id=callback.from_user.id,
            place_id=place_id,
            tier=normalized_tier,
            source=source,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to create mock payment intent place_id=%s tier=%s", place_id, normalized_tier)
        await callback.answer("Не вдалося підготувати оплату. Спробуй ще раз.", show_alert=True)
        return

    if not callback.message:
        await callback.answer("Оплата підготовлена")
        return

    provider = str(intent.get("provider") or "").strip().lower()
    rows = await cabinet_service.list_user_businesses(callback.from_user.id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == place_id), None)
    place_name = html.escape(str(item.get("place_name") if item else "вашого закладу"))
    place_name_plain = str(item.get("place_name") if item else "вашого закладу")
    back_cb = CB_MENU_PLANS if source == "plans" else f"{CB_MY_OPEN_PREFIX}{place_id}"

    if provider == PAYMENT_PROVIDER_TELEGRAM_STARS:
        invoice_payload = str(intent.get("invoice_payload") or "").strip()
        if not invoice_payload:
            await callback.answer("Не вдалося підготувати рахунок. Спробуй ще раз.", show_alert=True)
            return
        invoice_title = f"Тариф {PLAN_TITLES.get(normalized_tier, normalized_tier)}"
        invoice_description = (
            f"Підписка {PLAN_TITLES.get(normalized_tier, normalized_tier)} "
            f"для закладу «{place_name_plain}». Автопродовження щомісяця."
        )[:255]
        invoice_label = f"{PLAN_TITLES.get(normalized_tier, normalized_tier)} ({intent['amount_stars']}⭐)"
        try:
            invoice_link = await callback.message.bot.create_invoice_link(
                title=invoice_title[:32],
                description=invoice_description,
                payload=invoice_payload,
                currency="XTR",
                prices=[LabeledPrice(label=invoice_label, amount=int(intent["amount_stars"]))],
                provider_token=None,
                subscription_period=SUBSCRIPTION_PERIOD_SECONDS,
            )
        except Exception:
            logger.exception(
                "Failed to create Telegram Stars invoice link place_id=%s tier=%s chat_id=%s",
                place_id,
                normalized_tier,
                callback.message.chat.id,
            )
            await callback.answer("Не вдалося підготувати посилання на оплату. Спробуй ще раз.", show_alert=True)
            return

        await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
        await ui_render(
            callback.message.bot,
            chat_id=callback.message.chat.id,
            prefer_message_id=callback.message.message_id,
            text=(
                f"💳 <b>Підписка {PLAN_TITLES.get(normalized_tier, normalized_tier)}</b>\n\n"
                f"Заклад: <b>{place_name}</b>\n"
                f"Тариф: <b>{PLAN_TITLES.get(normalized_tier, normalized_tier)}</b>\n\n"
                f"Сума: <b>{intent['amount_stars']}⭐ / 30 днів</b>\n"
                "Автопродовження: <b>увімкнено Telegram</b>\n\n"
                "Натисни «Оплатити підписку», щоб підтвердити перший платіж."
            ),
            reply_markup=build_stars_payment_keyboard(
                pay_url=str(invoice_link),
                place_id=place_id,
                tier=normalized_tier,
                source=source,
                back_callback_data=back_cb,
            ),
        )
        await callback.answer("Посилання на оплату підготовлено")
        return

    if provider != PAYMENT_PROVIDER_MOCK:
        await callback.answer("Невідомий провайдер оплати в конфігурації.", show_alert=True)
        return

    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await ui_render(
        callback.message.bot,
        chat_id=callback.message.chat.id,
        prefer_message_id=callback.message.message_id,
        text=(
            f"💳 <b>Оплата тарифу {PLAN_TITLES.get(normalized_tier, normalized_tier)}</b>\n\n"
            f"Заклад: <b>{place_name}</b>\n"
            f"Сума: <b>{intent['amount_stars']}⭐</b>\n"
            "Режим: <b>TEST / MOCK</b>\n\n"
            "Обери результат симуляції оплати:"
        ),
        reply_markup=build_mock_payment_keyboard(
            place_id=place_id,
            tier=normalized_tier,
            external_payment_id=str(intent["external_payment_id"]),
            source=source,
        ),
    )
    await callback.answer("Оплата підготовлена")


@router.callback_query(F.data.startswith(CB_PAYMENT_RESULT_PREFIX))
async def cb_mock_payment_result(callback: CallbackQuery) -> None:
    payload = callback.data.removeprefix(CB_PAYMENT_RESULT_PREFIX).split(":")
    if len(payload) != 5:
        await callback.answer("Некоректні дані", show_alert=True)
        return
    try:
        place_id = int(payload[0])
    except Exception:
        await callback.answer("Некоректний заклад", show_alert=True)
        return
    tier = payload[1]
    external_payment_id = payload[2]
    result = payload[3]
    source = payload[4] or "card"

    try:
        outcome = await cabinet_service.apply_mock_payment_result(
            tg_user_id=callback.from_user.id,
            place_id=place_id,
            tier=tier,
            external_payment_id=external_payment_id,
            result=result,
        )
    except (ValidationError, NotFoundError, AccessDeniedError) as error:
        await callback.answer(str(error), show_alert=True)
        return

    if not callback.message:
        await callback.answer("Готово")
        return

    rows = await cabinet_service.list_user_businesses(callback.from_user.id)
    item = next((row for row in rows if int(row.get("place_id") or 0) == place_id), None)
    place_name = html.escape(str(item.get("place_name") if item else "вашого закладу"))
    back_cb = CB_MENU_PLANS if source == "plans" else f"{CB_MY_OPEN_PREFIX}{place_id}"
    current_tier = str(item.get("tier") if item else "free")

    if outcome.get("duplicate"):
        note = "ℹ️ Цю подію вже оброблено раніше."
    elif result == "success":
        note = "✅ Оплату успішно імітовано. Тариф активовано."
    elif result == "cancel":
        note = "ℹ️ Оплату скасовано. Тариф не змінено."
    else:
        note = "⚠️ Імітація помилки оплати. Тариф не змінено."

    await bind_ui_message_id(callback.message.chat.id, callback.message.message_id)
    await ui_render(
        callback.message.bot,
        chat_id=callback.message.chat.id,
        prefer_message_id=callback.message.message_id,
        text=f"{note}\n\nОбери тариф для <b>{place_name}</b>:",
        reply_markup=build_plan_keyboard(
            place_id,
            current_tier,
            current_status=str(item.get("subscription_status") if item else "inactive"),
            current_expires_at=str(item.get("subscription_expires_at") if item else "").strip() or None,
            back_callback_data=back_cb,
            source=source,
        ),
    )
    await callback.answer("Готово")


@router.pre_checkout_query()
async def on_pre_checkout_query(pre_checkout_query: PreCheckoutQuery) -> None:
    tg_user_id = pre_checkout_query.from_user.id if pre_checkout_query.from_user else 0
    try:
        await cabinet_service.validate_telegram_stars_pre_checkout(
            tg_user_id=int(tg_user_id),
            invoice_payload=str(pre_checkout_query.invoice_payload or ""),
            total_amount=int(pre_checkout_query.total_amount or 0),
            currency=str(pre_checkout_query.currency or ""),
            pre_checkout_query_id=str(pre_checkout_query.id or ""),
        )
    except (ValidationError, AccessDeniedError, NotFoundError) as error:
        message = str(error).strip() or "Оплату неможливо підтвердити."
        if len(message) > 180:
            message = message[:177] + "..."
        await pre_checkout_query.answer(ok=False, error_message=message)
        return
    except Exception:
        logger.exception(
            "Unexpected pre_checkout failure query_id=%s from_user=%s",
            pre_checkout_query.id,
            tg_user_id,
        )
        await pre_checkout_query.answer(ok=False, error_message="Технічна помилка. Спробуй ще раз.")
        return
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    payment = message.successful_payment
    if not payment:
        return
    await try_delete_user_message(message)
    tg_user_id = message.from_user.id if message.from_user else message.chat.id
    raw_payload_json = None
    try:
        raw_payload_json = payment.model_dump_json(exclude_none=True)
    except Exception:
        raw_payload_json = None

    try:
        outcome = await cabinet_service.apply_telegram_stars_successful_payment(
            tg_user_id=int(tg_user_id),
            invoice_payload=str(payment.invoice_payload or ""),
            total_amount=int(payment.total_amount or 0),
            currency=str(payment.currency or ""),
            subscription_expiration_date=(
                int(payment.subscription_expiration_date)
                if payment.subscription_expiration_date is not None
                else None
            ),
            is_recurring=bool(payment.is_recurring) if payment.is_recurring is not None else None,
            is_first_recurring=(
                bool(payment.is_first_recurring) if payment.is_first_recurring is not None else None
            ),
            telegram_payment_charge_id=str(payment.telegram_payment_charge_id or ""),
            provider_payment_charge_id=str(payment.provider_payment_charge_id or ""),
            raw_payload_json=raw_payload_json,
        )
    except (ValidationError, AccessDeniedError, NotFoundError) as error:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text=(
                "⚠️ Не вдалося застосувати оплату автоматично.\n"
                "Напиши адміністратору і додай скрін цього повідомлення.\n\n"
                f"Деталі: {html.escape(str(error))}"
            ),
            reply_markup=build_main_menu(tg_user_id),
        )
        return
    except Exception:
        logger.exception("Failed to apply successful payment for chat_id=%s", message.chat.id)
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text=(
                "⚠️ Сталась технічна помилка при обробці оплати.\n"
                "Напиши адміністратору і додай скрін цього повідомлення."
            ),
            reply_markup=build_main_menu(tg_user_id),
        )
        return

    place_id = int(outcome.get("place_id") or 0)
    source = str(outcome.get("source") or "card")
    rows = await cabinet_service.list_user_businesses(int(tg_user_id))
    item = next((row for row in rows if int(row.get("place_id") or 0) == place_id), None)
    place_name = html.escape(str(item.get("place_name") if item else "вашого закладу"))
    current_tier = str(item.get("tier") if item else "free")
    back_cb = CB_MENU_PLANS if source == "plans" else f"{CB_MY_OPEN_PREFIX}{place_id}"

    note = (
        "ℹ️ Цей платіж уже був оброблений раніше."
        if outcome.get("duplicate")
        else "✅ Оплату отримано. Тариф активовано."
    )
    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        text=f"{note}\n\nОбери тариф для <b>{place_name}</b>:",
        reply_markup=build_plan_keyboard(
            place_id,
            current_tier,
            current_status=str(item.get("subscription_status") if item else "inactive"),
            current_expires_at=str(item.get("subscription_expires_at") if item else "").strip() or None,
            back_callback_data=back_cb,
            source=source,
        ),
    )


@router.message(F.refunded_payment)
async def on_refunded_payment(message: Message) -> None:
    refunded = getattr(message, "refunded_payment", None)
    if not refunded:
        return
    await try_delete_user_message(message)
    tg_user_id = message.from_user.id if message.from_user else message.chat.id

    raw_payload_json = None
    try:
        raw_payload_json = refunded.model_dump_json(exclude_none=True)
    except Exception:
        raw_payload_json = None

    try:
        outcome = await cabinet_service.apply_telegram_stars_refund_update(
            tg_user_id=int(tg_user_id),
            invoice_payload=str(getattr(refunded, "invoice_payload", "") or ""),
            total_amount=int(getattr(refunded, "total_amount", 0) or 0),
            currency=str(getattr(refunded, "currency", "") or ""),
            telegram_payment_charge_id=str(getattr(refunded, "telegram_payment_charge_id", "") or ""),
            provider_payment_charge_id=str(getattr(refunded, "provider_payment_charge_id", "") or ""),
            raw_payload_json=raw_payload_json,
        )
    except (ValidationError, AccessDeniedError, NotFoundError) as error:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text=(
                "⚠️ Не вдалося застосувати повернення автоматично.\n"
                "Напиши адміністратору і додай скрін цього повідомлення.\n\n"
                f"Деталі: {html.escape(str(error))}"
            ),
            reply_markup=build_main_menu(tg_user_id),
        )
        return
    except Exception:
        logger.exception("Failed to apply refunded payment for chat_id=%s", message.chat.id)
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text=(
                "⚠️ Сталась технічна помилка при обробці повернення.\n"
                "Напиши адміністратору і додай скрін цього повідомлення."
            ),
            reply_markup=build_main_menu(tg_user_id),
        )
        return

    place_id = int(outcome.get("place_id") or 0)
    rows = await cabinet_service.list_user_businesses(int(tg_user_id))
    item = next((row for row in rows if int(row.get("place_id") or 0) == place_id), None)
    place_name = html.escape(str(item.get("place_name") if item else "вашого закладу"))
    current_tier = str(item.get("tier") if item else "free")
    back_cb = f"{CB_MY_OPEN_PREFIX}{place_id}" if place_id > 0 else CB_MENU_PLANS

    note = (
        "ℹ️ Це повернення вже було оброблено раніше."
        if outcome.get("duplicate")
        else "↩️ Повернення отримано. Тариф скасовано."
    )
    if place_id > 0:
        await ui_render(
            message.bot,
            chat_id=message.chat.id,
            text=f"{note}\n\nОбери тариф для <b>{place_name}</b>:",
            reply_markup=build_plan_keyboard(
                place_id,
                current_tier,
                current_status=str(item.get("subscription_status") if item else "inactive"),
                current_expires_at=str(item.get("subscription_expires_at") if item else "").strip() or None,
                back_callback_data=back_cb,
                source="card",
            ),
        )
        return

    await ui_render(
        message.bot,
        chat_id=message.chat.id,
        text=note,
        reply_markup=build_main_menu(tg_user_id),
    )
