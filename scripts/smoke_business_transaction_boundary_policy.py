#!/usr/bin/env python3
"""
Static smoke-check: business transaction boundary policy.

Policy goals:
- DB transactions (`BEGIN`) are allowed only in business repository layer.
- Business repository layer must not depend on Telegram/API client calls.
- Business service layer must not open raw SQL transactions.

This keeps network/Telegram operations outside transaction scopes and
reduces `database is locked` risk under multi-process load.

Run:
  python3 scripts/smoke_business_transaction_boundary_policy.py
"""

from __future__ import annotations

from pathlib import Path
import re


def _resolve(path_rel: str) -> Path:
    candidates = []
    try:
        candidates.append(Path(__file__).resolve().parents[1] / path_rel)
    except Exception:
        pass
    candidates.extend(
        [
            Path.cwd() / path_rel,
            Path("/app") / path_rel,
        ]
    )
    for p in candidates:
        if p.exists():
            return p
    return candidates[0] if candidates else Path(path_rel)


REPO_FILE = _resolve("src/business/repository.py")
SERVICE_FILE = _resolve("src/business/service.py")

TELEGRAM_MARKERS = (
    "aiogram",
    "Bot(",
    ".send_message(",
    ".edit_message_text(",
    ".answer(",
    "telegram",
)

BEGIN_RE = re.compile(r"await\s+db\.execute\(\s*\"BEGIN\"", re.IGNORECASE)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _read(path: Path) -> str:
    _assert(path.exists(), f"file not found: {path}")
    return path.read_text(encoding="utf-8")


def main() -> None:
    repo_text = _read(REPO_FILE)
    service_text = _read(SERVICE_FILE)

    violations: list[str] = []

    # 1) Repository is DB-only: no Telegram/API client calls/imports.
    lower_repo = repo_text.lower()
    for marker in TELEGRAM_MARKERS:
        if marker.lower() in lower_repo:
            violations.append(f"{REPO_FILE}: forbidden telegram/api marker `{marker}` in repository layer")

    # 2) Service layer should not open raw SQL transactions directly.
    if BEGIN_RE.search(service_text):
        violations.append(f"{SERVICE_FILE}: raw SQL transaction BEGIN is forbidden in service layer")

    # 3) Ensure repository still owns explicit BEGIN points (sanity check).
    begin_count_repo = len(BEGIN_RE.findall(repo_text))
    if begin_count_repo == 0:
        violations.append(f"{REPO_FILE}: expected at least one explicit BEGIN in repository layer")

    if violations:
        raise SystemExit("ERROR: business transaction boundary policy violation(s):\n" + "\n".join(f"- {v}" for v in violations))

    print("OK: business transaction boundary policy smoke passed.")


if __name__ == "__main__":
    main()
