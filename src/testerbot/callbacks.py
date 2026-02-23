"""Helpers for callback-data extraction and inventory parsing."""

from __future__ import annotations

import re
from pathlib import Path


def extract_callback_data(button) -> str | None:
    """Best-effort extraction of callback_data from Telethon button wrappers."""
    if button is None:
        return None
    raw = getattr(button, "data", None)
    if raw is None:
        inner = getattr(button, "button", None)
        raw = getattr(inner, "data", None) if inner is not None else None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8", errors="ignore")
        except Exception:
            return None
    value = str(raw).strip()
    return value or None


def extract_message_callbacks(message) -> set[str]:
    values: set[str] = set()
    buttons = getattr(message, "buttons", None) or []
    for row in buttons:
        for btn in row:
            data = extract_callback_data(btn)
            if data:
                values.add(data)
    return values


_EQ_RE = re.compile(r'F\.data\s*==\s*"([^"]+)"')
_EQ_CONST_RE = re.compile(r"F\.data\s*==\s*([A-Z][A-Z0-9_]+)")
_SW_RE = re.compile(r'F\.data\.startswith\("([^"]+)"\)')
_SW_CONST_RE = re.compile(r"F\.data\.startswith\(([A-Z][A-Z0-9_]+)\)")
_RG_RE = re.compile(r'F\.data\.regexp\(r"([^"]+)"\)')
_RG_CONST_RE = re.compile(r"F\.data\.regexp\(([A-Z][A-Z0-9_]+)\)")
_CONST_RE = re.compile(r"""^([A-Z][A-Z0-9_]+)\s*=\s*(['"])(.*?)\2\s*$""", re.MULTILINE)


def _parse_constants(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, _quote, value in _CONST_RE.findall(text):
        cleaned = str(value or "").strip()
        if cleaned:
            values[name] = cleaned
    return values


_READ_ONLY_EXCLUDE_EQ: dict[str, set[str]] = {
    "resident": {
        # Vote toggles mutate resident state.
        "menu_vote_heating_no",
        "menu_vote_heating_yes",
        "menu_vote_water_no",
        "menu_vote_water_yes",
        "vote_heating_no",
        "vote_heating_yes",
        "vote_water_no",
        "vote_water_yes",
        # Notification toggles mutate subscriber settings.
        "notif_toggle_alert",
        "notif_toggle_light",
        "notif_toggle_offers_digest",
        "notif_toggle_schedule",
        "notif_toggle_sponsored",
        # Quiet-hours help button is UI-copy/config dependent.
        "quiet_info",
    },
    "admin": {
        # Explicit control-plane mutating actions.
        "admin_toggle_light",
        "admin_broadcast",
        "admin_broadcast_confirm",
        "admin_offers_digest",
        "admin_offers_digest_confirm",
        "admin_sensors_unfreeze_all",
    },
    "business": set(),
}


_READ_ONLY_EXCLUDE_SW: dict[str, set[str]] = {
    "resident": {
        # Resident preference/interaction mutations.
        "building_",
        "section_",
        "quiet_",
        "like_",
        "unlike_",
        "shelter_like_",
        "shelter_unlike_",
        # Business CTA callbacks (click/open counters, external URLs).
        "pcall_",
        "pchat_",
        "pcoupon_",
        "plink_",
        "plogo_",
        "pmenu_",
        "pmimg1_",
        "pmimg2_",
        "porder_",
        "pph1_",
        "pph2_",
        "pph3_",
        # Place report submission flow.
        "plrep_",
        "plrep_cancel_",
    },
    "admin": {
        # Sensor freeze/unfreeze mutate sensor state.
        "admin_sensor_freeze|",
        "admin_sensor_unfreeze|",
        "admin_sensors_freeze_all|",
    },
    "business": {
        # Business cabinet mutating actions excluded from read-only coverage gate.
        "be:",
        "bef:",
        "bmod_",
        "bm:",
        "bp:",
        "bp_cancel:",
        "btok",
    },
}


_READ_ONLY_EXCLUDE_RG: dict[str, set[str]] = {
    "resident": {
        # Gallery callbacks are data-dependent (shown only when media exists).
        r"^pgm_\d+_\d+$",
    },
}


_READ_ONLY_INCLUDE_EQ: dict[str, set[str]] = {
    "admin": {
        "admin_refresh",
        "admin_subs",
        "admin_sensors",
        "admin_jobs",
        "admin_jobs_export",
        "admin_cancel",
        "admin_business",
        "abiz_mod",
        "abiz_reports",
        "abiz_support",
        "abiz_tok_menu",
        "abiz_tok_list",
        "abiz_subs",
        "abiz_subs_export",
    },
    "business": {
        "bmenu:add",
        "bmenu:attach",
        "bmenu:cancel",
        "bmenu:home",
        "bmenu:mine",
        "bmenu:plans",
    },
}


_READ_ONLY_INCLUDE_SW: dict[str, set[str]] = {
    "admin": {
        "admin_jobs_page|",
        "admin_sensors_page|",
        "admin_sensor|",
        "abiz_tokv_s|",
    },
    "business": {
        "bp_menu:",
    },
}


def parse_callback_inventory(repo_root: Path) -> dict[str, dict[str, set[str]]]:
    """Extract callback matcher patterns from resident/admin/business handlers."""
    mapping = {
        "resident": repo_root / "src" / "handlers.py",
        "admin": repo_root / "src" / "admin" / "handlers.py",
        "business": repo_root / "src" / "business" / "handlers.py",
    }
    out: dict[str, dict[str, set[str]]] = {}
    for key, path in mapping.items():
        text = path.read_text(encoding="utf-8")
        constants = _parse_constants(text)
        eq = set(_EQ_RE.findall(text))
        sw = set(_SW_RE.findall(text))
        rg = set(_RG_RE.findall(text))

        for name in _EQ_CONST_RE.findall(text):
            value = constants.get(name)
            if value:
                eq.add(value)
        for name in _SW_CONST_RE.findall(text):
            value = constants.get(name)
            if value:
                sw.add(value)
        for name in _RG_CONST_RE.findall(text):
            value = constants.get(name)
            if value:
                rg.add(value)

        out[key] = {
            "eq": eq,
            "startswith": sw,
            "regexp": rg,
        }
    return out


def filter_read_only_inventory(
    inventory: dict[str, dict[str, set[str]]],
) -> dict[str, dict[str, set[str]]]:
    """Return read-only coverage subset (exclude known mutating callbacks)."""
    filtered: dict[str, dict[str, set[str]]] = {}
    for bot_name, rules in inventory.items():
        eq = set(rules.get("eq", set()))
        sw = set(rules.get("startswith", set()))
        rg = set(rules.get("regexp", set()))

        eq -= _READ_ONLY_EXCLUDE_EQ.get(bot_name, set())
        sw -= _READ_ONLY_EXCLUDE_SW.get(bot_name, set())
        rg -= _READ_ONLY_EXCLUDE_RG.get(bot_name, set())

        include_eq = _READ_ONLY_INCLUDE_EQ.get(bot_name)
        include_sw = _READ_ONLY_INCLUDE_SW.get(bot_name)
        if include_eq is not None:
            eq &= include_eq
        if include_sw is not None:
            sw &= include_sw

        filtered[bot_name] = {"eq": eq, "startswith": sw, "regexp": rg}
    return filtered
