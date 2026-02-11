#!/usr/bin/env python3
"""
Static smoke-check: enforce retry policy for business repository writes.

Policy:
- no direct DML via `await db.execute/await db.executemany` outside explicit
  allowlisted transactional methods that implement their own retry loop.
- preferred path is `execute_write_with_retry(...)`.

Run:
  python3 scripts/smoke_business_write_retry_policy.py
"""

from __future__ import annotations

from pathlib import Path
import re


REPO_FILE = Path("src/business/repository.py")

# Methods that intentionally use manual transactions + explicit retry loops.
ALLOWED_DIRECT_DML_FUNCS = {
    "rotate_active_claim_tokens_bulk",
    "delete_place_draft",
    "write_audit_logs_bulk",
}

DEF_RE = re.compile(r"^\s*async\s+def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
DML_RE = re.compile(
    r"await\s+db\.(?:execute|executemany)\(\s*\"(?:INSERT|UPDATE|DELETE)\b",
    re.IGNORECASE,
)


def main() -> None:
    if not REPO_FILE.exists():
        raise SystemExit(f"ERROR: file not found: {REPO_FILE}")

    lines = REPO_FILE.read_text(encoding="utf-8").splitlines()
    current_func = "<module>"
    violations: list[str] = []

    for lineno, line in enumerate(lines, start=1):
        m = DEF_RE.match(line)
        if m:
            current_func = m.group(1)
            continue

        if not DML_RE.search(line):
            continue

        if current_func in ALLOWED_DIRECT_DML_FUNCS:
            continue

        violations.append(
            f"{REPO_FILE}:{lineno}: direct DML in disallowed function `{current_func}` -> {line.strip()}"
        )

    if violations:
        msg = "\n".join(violations)
        raise SystemExit(
            "ERROR: business repository write-retry policy violation(s) detected:\n"
            f"{msg}"
        )

    print("OK: business repository write-retry policy smoke passed.")


if __name__ == "__main__":
    main()
