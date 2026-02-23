#!/usr/bin/env python3
"""
Static policy smoke for testerbot idempotence/read-only contract.

Goals:
- prevent accidental mutation actions in E2E scenarios;
- ensure business scenario keeps explicit cancel paths for mutating flows.
"""

from __future__ import annotations

import re
from pathlib import Path


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _find_forbidden_tokens(text: str, forbidden: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    lowered = text.casefold()
    for token in forbidden:
        if token.casefold() in lowered:
            hits.append(token)
    return hits


def _extract_send_message_literals(text: str) -> list[str]:
    # Simple static extraction of send_message("<literal>") calls.
    pattern = re.compile(r"send_message\(\s*([\"'])(.*?)\1", re.DOTALL)
    return [m.group(2) for m in pattern.finditer(text)]


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    scenarios_dir = repo_root / "src" / "testerbot" / "scenarios"
    resident_path = scenarios_dir / "resident.py"
    business_path = scenarios_dir / "business.py"
    admin_path = scenarios_dir / "admin.py"

    resident = _read(resident_path)
    business = _read(business_path)
    admin = _read(admin_path)

    forbidden_mutation_tokens = (
        "💔 Забрати лайк",
        "❤️ Подобається",
        "shelter_like_",
        "shelter_unlike_",
        "callback_data=f\"like_",
        "callback_data=f\"unlike_",
        "↩️ Refund",
        "Підтвердити заявку",
    )

    for name, text in (("resident", resident), ("business", business), ("admin", admin)):
        hits = _find_forbidden_tokens(text, forbidden_mutation_tokens)
        _assert(
            not hits,
            f"{name} scenario contains forbidden mutation tokens: {hits}",
        )

    # Resident scenario must keep input surface deterministic.
    send_messages = _extract_send_message_literals(resident)
    allowed_literals = {"/start", "сирники"}
    unexpected = [value for value in send_messages if value not in allowed_literals]
    _assert(
        not unexpected,
        f"resident scenario has unexpected send_message literals: {unexpected}",
    )

    # Business scenario may open mutating flows only with immediate cancel guard.
    _assert(
        "Додати бізнес" in business and "business add cancel" in business,
        "business scenario must open add-flow only with explicit cancel branch",
    )
    _assert(
        "Прив'язати бізнес" in business and "business attach cancel" in business,
        "business scenario must open attach-flow only with explicit cancel branch",
    )
    _assert(
        "business owner card" in business and "business owner card open plans" in business,
        "business scenario must cover owner card -> plan menu read-only navigation",
    )
    _assert(
        "business plans place menu" in business and "business plans place back" in business,
        "business scenario must cover plans list -> place plan menu -> back",
    )
    business_sends = _extract_send_message_literals(business)
    _assert(
        business_sends == ["/start"],
        f"business scenario must only send /start, got: {business_sends}",
    )

    # Admin scenario should remain read-only (no data-entry message sends).
    admin_sends = _extract_send_message_literals(admin)
    _assert(
        admin_sends == ["/start"],
        f"admin scenario must only send /start, got: {admin_sends}",
    )

    print("OK: testerbot idempotence policy smoke passed.")


if __name__ == "__main__":
    main()
