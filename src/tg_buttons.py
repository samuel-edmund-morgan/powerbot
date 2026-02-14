"""Telegram UI helpers.

We keep this module tiny and dependency-free (besides aiogram types) so we can
gradually adopt new Telegram Bot API fields (e.g. button `style`) without
refactoring the whole codebase at once.

Telegram Bot API (InlineKeyboardButton.style):
  - "danger"  (red)
  - "success" (green)
  - "primary" (blue)
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton

STYLE_DANGER = "danger"
STYLE_SUCCESS = "success"
STYLE_PRIMARY = "primary"

_ALLOWED_STYLES = {STYLE_DANGER, STYLE_SUCCESS, STYLE_PRIMARY}


def _normalize_style(style: str | None) -> str | None:
    if not style:
        return None
    value = str(style).strip().lower()
    return value if value in _ALLOWED_STYLES else None


def ikb(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    style: str | None = None,
    icon_custom_emoji_id: str | None = None,
) -> InlineKeyboardButton:
    """Create InlineKeyboardButton with optional `style`.

    Backward-compatible with older aiogram versions that don't support new fields.
    """
    kwargs: dict[str, object] = {"text": str(text)}
    if callback_data is not None:
        kwargs["callback_data"] = str(callback_data)
    if url is not None:
        kwargs["url"] = str(url)

    normalized_style = _normalize_style(style)
    if normalized_style:
        kwargs["style"] = normalized_style
    if icon_custom_emoji_id:
        kwargs["icon_custom_emoji_id"] = str(icon_custom_emoji_id)

    try:
        return InlineKeyboardButton(**kwargs)  # type: ignore[arg-type]
    except TypeError:
        # Older aiogram types may not accept new fields.
        kwargs.pop("style", None)
        kwargs.pop("icon_custom_emoji_id", None)
        return InlineKeyboardButton(**kwargs)  # type: ignore[arg-type]
    except Exception:
        # Validation error, etc. Fall back to plain button.
        kwargs.pop("style", None)
        kwargs.pop("icon_custom_emoji_id", None)
        return InlineKeyboardButton(**kwargs)  # type: ignore[arg-type]

