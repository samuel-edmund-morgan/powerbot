#!/usr/bin/env python3
"""
Static smoke-check: SQLite transaction boundary policy for shared DB layer.

Policy goals:
- `src/database.py` remains DB-only (no Telegram/API/network client calls).
- Explicit SQL transactions (`BEGIN`) are allowed only in DB-oriented modules.
- Business service layer must not open raw SQL transactions directly.

This keeps network operations out of transaction-heavy paths and lowers
`database is locked` risk under multiple bot processes.

Run:
  python3 scripts/smoke_sqlite_transaction_boundary_policy.py
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


DB_FILE = _resolve("src/database.py")
BUSINESS_REPO_FILE = _resolve("src/business/repository.py")
BUSINESS_SERVICE_FILE = _resolve("src/business/service.py")

FORBIDDEN_NETWORK_MARKERS = (
    "aiogram",
    "Bot(",
    ".send_message(",
    ".edit_message_text(",
    ".answer(",
    "aiohttp.clientsession(",
    "requests.",
    "httpx.",
    "urllib.request",
)

BEGIN_RE = re.compile(r"await\s+db\.execute\(\s*\"BEGIN(?:\s+IMMEDIATE)?\"", re.IGNORECASE)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _read(path: Path) -> str:
    _assert(path.exists(), f"file not found: {path}")
    return path.read_text(encoding="utf-8")


def _check_no_network_markers(path: Path, text: str) -> list[str]:
    violations: list[str] = []
    lower_text = text.lower()
    for marker in FORBIDDEN_NETWORK_MARKERS:
        if marker.lower() in lower_text:
            violations.append(f"{path}: forbidden network marker `{marker}` in DB-layer")
    return violations


def main() -> None:
    db_text = _read(DB_FILE)
    repo_text = _read(BUSINESS_REPO_FILE)
    service_text = _read(BUSINESS_SERVICE_FILE)

    violations: list[str] = []

    # 1) DB layer must stay network-free.
    violations.extend(_check_no_network_markers(DB_FILE, db_text))
    violations.extend(_check_no_network_markers(BUSINESS_REPO_FILE, repo_text))

    # 2) Business service should not open SQL transactions.
    if BEGIN_RE.search(service_text):
        violations.append(f"{BUSINESS_SERVICE_FILE}: raw SQL transaction BEGIN is forbidden in service layer")

    # 3) Sanity: transaction scopes should exist in DB-oriented layers.
    begin_count_db = len(BEGIN_RE.findall(db_text))
    begin_count_repo = len(BEGIN_RE.findall(repo_text))
    if begin_count_db + begin_count_repo == 0:
        violations.append(
            "expected explicit BEGIN in DB/repository layers (sanity check failed)"
        )

    if violations:
        raise SystemExit(
            "ERROR: sqlite transaction boundary policy violation(s):\n"
            + "\n".join(f"- {v}" for v in violations)
        )

    print("OK: sqlite transaction boundary policy smoke passed.")


if __name__ == "__main__":
    main()
