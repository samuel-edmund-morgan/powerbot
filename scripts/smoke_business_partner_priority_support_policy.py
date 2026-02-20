#!/usr/bin/env python3
"""
Static smoke-check: Partner priority support flow.

Policy:
- DB schema has business_support_requests table + indexes.
- Businessbot owner card has Partner-only support CTA.
- Businessbot submits support request + enqueues admin job `admin_partner_support_alert`.
- Admin worker handles `admin_partner_support_alert` and routes to adminbot support queue callbacks.
- Adminbot exposes `Підтримка Partner` queue with resolve callbacks.
"""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: file not found: {path}")
    return path.read_text(encoding="utf-8")


def _must(text: str, token: str, *, errors: list[str], label: str) -> None:
    if token not in text:
        errors.append(f"{label}: missing token: {token}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    schema = _read(root / "schema.sql")
    db_py = _read(root / "src" / "database.py")
    business_handlers = _read(root / "src" / "business" / "handlers.py")
    worker = _read(root / "src" / "admin_jobs_worker.py")
    admin_handlers = _read(root / "src" / "admin" / "handlers.py")

    errors: list[str] = []

    _must(schema, "CREATE TABLE IF NOT EXISTS business_support_requests", errors=errors, label="schema.sql")
    _must(
        schema,
        "idx_business_support_requests_status_created",
        errors=errors,
        label="schema.sql",
    )
    _must(schema, "idx_business_support_requests_place_id", errors=errors, label="schema.sql")

    _must(db_py, "async def create_business_support_request(", errors=errors, label="src/database.py")
    _must(db_py, "async def list_business_support_requests(", errors=errors, label="src/database.py")
    _must(
        db_py,
        "async def set_business_support_request_status(",
        errors=errors,
        label="src/database.py",
    )

    _must(
        business_handlers,
        'CB_PARTNER_SUPPORT_PREFIX = "bps:"',
        errors=errors,
        label="src/business/handlers.py",
    )
    _must(
        business_handlers,
        "🧑‍💼 Пріоритетна підтримка",
        errors=errors,
        label="src/business/handlers.py",
    )
    _must(
        business_handlers,
        "create_business_support_request(",
        errors=errors,
        label="src/business/handlers.py",
    )
    _must(
        business_handlers,
        '"admin_partner_support_alert"',
        errors=errors,
        label="src/business/handlers.py",
    )

    _must(
        worker,
        'JOB_KIND_ADMIN_PARTNER_SUPPORT_ALERT = "admin_partner_support_alert"',
        errors=errors,
        label="src/admin_jobs_worker.py",
    )
    _must(worker, "_handle_admin_partner_support_alert", errors=errors, label="src/admin_jobs_worker.py")
    _must(worker, "abiz_support_jump|", errors=errors, label="src/admin_jobs_worker.py")
    _must(worker, 'callback_data="abiz_support"', errors=errors, label="src/admin_jobs_worker.py")
    _must(
        worker,
        '_build_adminbot_prefixed_start_url(admin_bot_username, "bsup", support_request_id)',
        errors=errors,
        label="src/admin_jobs_worker.py",
    )

    _must(admin_handlers, 'CB_BIZ_SUPPORT = "abiz_support"', errors=errors, label="src/admin/handlers.py")
    _must(admin_handlers, "def _render_business_support(", errors=errors, label="src/admin/handlers.py")
    _must(
        admin_handlers,
        "cb_business_support_resolve",
        errors=errors,
        label="src/admin/handlers.py",
    )

    if errors:
        raise SystemExit("ERROR: business partner support policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: business partner support policy smoke passed.")


if __name__ == "__main__":
    main()
