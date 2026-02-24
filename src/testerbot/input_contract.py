"""Input-flow coverage contract for testerbot full-coverage artifact."""

from __future__ import annotations


# NOTE:
# We gate only flows that are guaranteed in deterministic read-only scenarios.
# Mutating/FSM text-entry flows are intentionally excluded from this strict scope.
REQUIRED_INPUT_FLOWS: dict[str, set[str]] = {
    "resident": {
        "text:search_keyword",
    },
    "admin": {
        "command:/start",
    },
    "business": {
        "command:/start",
    },
}

