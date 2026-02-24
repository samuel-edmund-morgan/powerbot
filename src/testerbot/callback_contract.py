"""Centralized read-only callback coverage contract for testerbot."""

from __future__ import annotations


# Whitelisted read-only callback rules used by strict coverage inventory.
READ_ONLY_INCLUDE_EQ: dict[str, set[str]] = {
    "admin": {
        "admin_refresh",
        "admin_subs",
        "admin_sensors",
        "admin_jobs",
        "admin_jobs_export",
        "admin_cancel",
        "admin_business",
        "abiz_mod",
        "abiz_reports",
        "abiz_support",
        "abiz_tok_menu",
        "abiz_tok_list",
        "abiz_subs",
        "abiz_payments",
        "abiz_audit",
    },
    "business": {
        "bmenu:add",
        "bmenu:attach",
        "bmenu:cancel",
        "bmenu:home",
        "bmenu:mine",
        "bmenu:plans",
    },
}


READ_ONLY_INCLUDE_SW: dict[str, set[str]] = {
    "admin": {
        "admin_jobs_page|",
        "admin_sensors_page|",
        "admin_sensor|",
        "abiz_tokv_s|",
        "abiz_tokv_o|",
    },
    "business": {
        "bp_menu:",
    },
}


READ_ONLY_INCLUDE_RG: dict[str, set[str]] = {}


# Runtime coverage requirements consumed by smoke_testerbot_callback_coverage_runtime.py.
RUNTIME_COVERAGE_REQUIREMENTS: dict[str, dict[str, object]] = {
    "resident": {
        "min_clicked": 20,
        "clicked_eq": {
            "search_menu",
            "places_menu",
            "utilities_menu",
            "notifications_menu",
        },
        "clicked_prefix": {
            "places_cat_",
            "place_",
        },
        "seen_eq": set(),
        "seen_prefix": {
            "plrep_",
        },
    },
    "admin": {
        "min_clicked": 18,
        "clicked_eq": {
            "admin_jobs_export",
        },
        "clicked_prefix": {
            "admin_sensor|",
            "abiz_tokv_s|",
            "abiz_tokv_o|",
        },
        "seen_eq": {
            "abiz_payments",
            "abiz_audit",
        },
        "seen_prefix": set(),
    },
    "business": {
        "min_clicked": 10,
        "clicked_eq": {
            "bmenu:mine",
            "bmenu:plans",
            "bmenu:add",
            "bmenu:attach",
            "bmenu:cancel",
        },
        "clicked_prefix": {
            "bp_menu:",
        },
        "seen_eq": set(),
        "seen_prefix": {
            "bmy_o:",
        },
    },
}

