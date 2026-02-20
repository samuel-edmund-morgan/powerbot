#!/usr/bin/env python3
"""
Static smoke-check: partner offers digest job wiring.

Policy:
- Admin UI exposes digest action and enqueues `offers_digest` admin job.
- Worker supports `offers_digest` kind and applies opt-in + rate-limit helpers.
- Database exposes digest eligibility + sent-marking helpers.
"""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: file not found: {path}")
    return path.read_text(encoding="utf-8")


def _must(text: str, token: str, *, errors: list[str], where: str) -> None:
    if token not in text:
        errors.append(f"{where}: missing token: {token}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    errors: list[str] = []

    admin_handlers = _read(root / "src" / "admin" / "handlers.py")
    worker = _read(root / "src" / "admin_jobs_worker.py")
    database = _read(root / "src" / "database.py")

    _must(admin_handlers, "admin_offers_digest", errors=errors, where="src/admin/handlers.py")
    _must(admin_handlers, "admin_offers_digest_confirm", errors=errors, where="src/admin/handlers.py")
    _must(admin_handlers, 'create_admin_job(\n        "offers_digest",', errors=errors, where="src/admin/handlers.py")
    _must(admin_handlers, "📬 Дайджест акцій", errors=errors, where="src/admin/handlers.py")

    _must(worker, 'JOB_KIND_OFFERS_DIGEST = "offers_digest"', errors=errors, where="src/admin_jobs_worker.py")
    _must(worker, "async def _handle_offers_digest(", errors=errors, where="src/admin_jobs_worker.py")
    _must(worker, "get_subscribers_for_offers_digest", errors=errors, where="src/admin_jobs_worker.py")
    _must(worker, "mark_offers_digest_sent", errors=errors, where="src/admin_jobs_worker.py")
    _must(worker, "elif kind == JOB_KIND_OFFERS_DIGEST:", errors=errors, where="src/admin_jobs_worker.py")

    _must(database, "def offers_digest_last_sent_at_key(chat_id: int) -> str:", errors=errors, where="src/database.py")
    _must(database, "async def get_subscribers_for_offers_digest(", errors=errors, where="src/database.py")
    _must(database, "async def mark_offers_digest_sent(", errors=errors, where="src/database.py")

    if errors:
        raise SystemExit(
            "ERROR: offers digest job policy violation(s):\n- "
            + "\n- ".join(errors)
        )

    print("OK: offers digest job policy smoke passed.")


if __name__ == "__main__":
    main()
