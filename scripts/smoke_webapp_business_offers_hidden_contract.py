#!/usr/bin/env python3
"""
Smoke: WebApp business-offers toggles must stay hidden when offers are not visible.

Checks:
- CSS has strong hidden rule for toggle rows.
- UI applies explicit display none/flex fallback in renderSettings.
"""

from __future__ import annotations

from pathlib import Path


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    styles = (repo_root / "webapp" / "styles.css").read_text(encoding="utf-8")
    ui_js = (repo_root / "webapp" / "ui.js").read_text(encoding="utf-8")

    _assert(
        ".toggle[hidden]" in styles and "display: none !important;" in styles,
        "styles.css must enforce .toggle[hidden] { display: none !important; }",
    )
    _assert(
        "sponsoredRow.style.display = businessOffersVisible ? \"flex\" : \"none\";" in ui_js,
        "ui.js must set sponsored row display explicitly",
    )
    _assert(
        "digestRow.style.display = businessOffersVisible ? \"flex\" : \"none\";" in ui_js,
        "ui.js must set digest row display explicitly",
    )
    print("OK: webapp business-offers hidden contract smoke passed.")


if __name__ == "__main__":
    main()
