"""Helpers for callback-data extraction and inventory parsing."""

from __future__ import annotations

import re
from pathlib import Path

from testerbot.callback_contract import (
    READ_ONLY_INCLUDE_EQ,
    READ_ONLY_INCLUDE_RG,
    READ_ONLY_INCLUDE_SW,
)


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
        # Business-admin actions intentionally out of read-only strict coverage.
        "abiz_categories",
        "abiz_category_add",
        "abiz_create_place",
        "abiz_payments_export",
        "abiz_places",
        "abiz_subs_export",
        "abiz_tok_gen",
        "abiz_tok_gen_all",
        "abiz_tok_gen_all_confirm",
        "admin_noop",
    },
    "business": {
        # Business-cabinet actions intentionally out of read-only strict coverage.
        "bbld_change",
        "bmenu:moderation",
        "bmenu:noop",
        "bmenu:tokens",
    },
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
        # Business-admin callbacks intentionally out of read-only strict coverage.
        "abiz_audit_page|",
        "abiz_categories_p|",
        "abiz_category_o|",
        "abiz_category_r|",
        "abiz_create_b|",
        "abiz_create_promo|",
        "abiz_create_sp|",
        "abiz_create_s|",
        "abiz_mod_approve|",
        "abiz_mod_jump|",
        "abiz_mod_page|",
        "abiz_mod_reject|",
        "abiz_pay_refundc|",
        "abiz_pay_refund|",
        "abiz_payments_page|",
        "abiz_places_delc|",
        "abiz_places_del|",
        "abiz_places_editb|",
        "abiz_places_editf|",
        "abiz_places_edit|",
        "abiz_places_f|",
        "abiz_places_hidec|",
        "abiz_places_hide|",
        "abiz_places_o|",
        "abiz_places_pp|",
        "abiz_places_promo_m|",
        "abiz_places_promo_s|",
        "abiz_places_pub|",
        "abiz_places_roc|",
        "abiz_places_ro|",
        "abiz_places_search|",
        "abiz_places_sp|",
        "abiz_places_s|",
        "abiz_reports_jump|",
        "abiz_reports_page|",
        "abiz_reports_resolve|",
        "abiz_subs_page|",
        "abiz_support_jump|",
        "abiz_support_page|",
        "abiz_support_resolve|",
        "abiz_tokg_pp|",
        "abiz_tokg_r|",
        "abiz_tokg_sp|",
        "abiz_tokg_s|",
        "abiz_tokv_pp|",
        "abiz_tokv_r|",
        "abiz_tokv_sp|",
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
        "bbld:",
        "bcat:",
        "bcatp:",
        "bebld:",
        "bebld_change:",
        "bec:",
        "bec_clear:",
        "begal:",
        "begal_add:",
        "begal_del:",
        "bfr:",
        "bfrc:",
        "bmy_o:",
        "bmy_p:",
        "bpayr:",
        "bplans_p:",
        "bps:",
        "bpsc:",
        "bqr:",
        "bqrkit:",
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

        include_eq = READ_ONLY_INCLUDE_EQ.get(bot_name)
        include_sw = READ_ONLY_INCLUDE_SW.get(bot_name)
        include_rg = READ_ONLY_INCLUDE_RG.get(bot_name)
        if include_eq is not None:
            eq &= include_eq
        if include_sw is not None:
            sw &= include_sw
        if include_rg is not None:
            rg &= include_rg

        filtered[bot_name] = {"eq": eq, "startswith": sw, "regexp": rg}
    return filtered
