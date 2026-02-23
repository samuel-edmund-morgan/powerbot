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
_SW_RE = re.compile(r'F\.data\.startswith\("([^"]+)"\)')
_RG_RE = re.compile(r'F\.data\.regexp\(r"([^"]+)"\)')


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
        out[key] = {
            "eq": set(_EQ_RE.findall(text)),
            "startswith": set(_SW_RE.findall(text)),
            "regexp": set(_RG_RE.findall(text)),
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

        filtered[bot_name] = {"eq": eq, "startswith": sw, "regexp": rg}
    return filtered
