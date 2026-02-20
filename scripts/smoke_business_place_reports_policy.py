#!/usr/bin/env python3
"""
Static smoke-check for resident "place report" -> admin moderation flow.

Policy:
- resident handlers expose callback `plrep_` and enqueue `admin_place_report_alert`.
- admin jobs worker knows `JOB_KIND_ADMIN_PLACE_REPORT_ALERT` and dispatches it.
- adminbot has reports menu/callbacks (`abiz_reports*`).
"""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: file not found: {path}")
    return path.read_text(encoding="utf-8")


def _must_contain(text: str, token: str, *, file_label: str, errors: list[str]) -> None:
    if token not in text:
        errors.append(f"{file_label}: missing token `{token}`")


def _must_contain_any(text: str, tokens: list[str], *, file_label: str, errors: list[str], label: str) -> None:
    if any(token in text for token in tokens):
        return
    joined = " OR ".join(f"`{token}`" for token in tokens)
    errors.append(f"{file_label}: missing {label} token ({joined})")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    schema_text = _read(root / "schema.sql")
    handlers_text = _read(root / "src/handlers.py")
    worker_text = _read(root / "src/admin_jobs_worker.py")
    admin_handlers_text = _read(root / "src/admin/handlers.py")

    errors: list[str] = []

    # Schema/runtime parity.
    _must_contain(schema_text, "CREATE TABLE IF NOT EXISTS place_reports", file_label="schema.sql", errors=errors)
    _must_contain(schema_text, "idx_place_reports_status_created", file_label="schema.sql", errors=errors)
    _must_contain(schema_text, "idx_place_reports_place_id", file_label="schema.sql", errors=errors)

    # Resident flow.
    _must_contain(handlers_text, 'callback_data=f"plrep_', file_label="src/handlers.py", errors=errors)
    _must_contain(handlers_text, r'F.data.regexp(r"^plrep_\d+$")', file_label="src/handlers.py", errors=errors)
    _must_contain(handlers_text, '"admin_place_report_alert"', file_label="src/handlers.py", errors=errors)
    _must_contain(
        handlers_text,
        "@router.message(StateFilter(None), F.text.in_(LEGACY_REPLY_TEXTS))",
        file_label="src/handlers.py",
        errors=errors,
    )
    _must_contain_any(
        handlers_text,
        [
            "@router.message(StateFilter(None), F.text, lambda message: message.chat.id not in search_waiting_users)",
            "@router.message(StateFilter(None), F.text)",
        ],
        file_label="src/handlers.py",
        errors=errors,
        label="generic text fallback",
    )

    # Worker flow.
    _must_contain(worker_text, 'JOB_KIND_ADMIN_PLACE_REPORT_ALERT = "admin_place_report_alert"', file_label="src/admin_jobs_worker.py", errors=errors)
    _must_contain(worker_text, "_handle_admin_place_report_alert", file_label="src/admin_jobs_worker.py", errors=errors)
    _must_contain(worker_text, "abiz_reports_jump|", file_label="src/admin_jobs_worker.py", errors=errors)
    _must_contain(worker_text, 'callback_data="abiz_reports"', file_label="src/admin_jobs_worker.py", errors=errors)

    # Adminbot flow.
    _must_contain(admin_handlers_text, 'CB_BIZ_REPORTS = "abiz_reports"', file_label="src/admin/handlers.py", errors=errors)
    _must_contain(admin_handlers_text, "CB_BIZ_REPORTS_RESOLVE_PREFIX", file_label="src/admin/handlers.py", errors=errors)
    _must_contain(admin_handlers_text, "def _render_business_reports(", file_label="src/admin/handlers.py", errors=errors)

    if errors:
        raise SystemExit("ERROR: place-report policy violation(s):\n- " + "\n- ".join(errors))

    print("OK: place-report policy smoke passed.")


if __name__ == "__main__":
    main()
