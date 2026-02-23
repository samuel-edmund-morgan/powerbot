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
