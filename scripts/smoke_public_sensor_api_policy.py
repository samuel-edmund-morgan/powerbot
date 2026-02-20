#!/usr/bin/env python3
"""
Static smoke-check: public sensor status API contract.

Policy:
- Public status API must use a dedicated read-only key (`SENSOR_PUBLIC_API_KEY`).
- Public routes must exist for all sensors and single sensor by numeric ID.
- Public status must be computed from heartbeat age only (freeze-independent).

Run:
  python3 scripts/smoke_public_sensor_api_policy.py
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
    candidates.extend([Path.cwd() / path_rel, Path("/app") / path_rel])
    for p in candidates:
        if p.exists():
            return p
    return candidates[0] if candidates else Path(path_rel)


CONFIG_FILE = _resolve("src/config.py")
API_FILE = _resolve("src/api_server.py")
SCHEMA_FILE = _resolve("schema.sql")


def _extract_function_body(text: str, func_name: str) -> str:
    marker = f"def {func_name}("
    alt_marker = f"async def {func_name}("
    start = text.find(marker)
    if start < 0:
        start = text.find(alt_marker)
    if start < 0:
        return ""
    tail = text[start:]
    m = re.search(
        r"^\n(?:async\s+def\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(|def\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\()",
        tail,
        flags=re.MULTILINE,
    )
    return tail if not m else tail[: m.start()]


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> None:
    _assert(CONFIG_FILE.exists(), f"file not found: {CONFIG_FILE}")
    _assert(API_FILE.exists(), f"file not found: {API_FILE}")
    _assert(SCHEMA_FILE.exists(), f"file not found: {SCHEMA_FILE}")

    cfg = CONFIG_FILE.read_text(encoding="utf-8")
    api = API_FILE.read_text(encoding="utf-8")
    schema = SCHEMA_FILE.read_text(encoding="utf-8")

    violations: list[str] = []

    # Config contract: dedicated public key.
    for snippet in (
        "sensor_public_api_key: str",
        'sensor_public_api_key=os.getenv("SENSOR_PUBLIC_API_KEY"',
    ):
        if snippet not in cfg:
            violations.append(f"{CONFIG_FILE}: missing snippet `{snippet}`")

    # Schema contract: stable external sensor ids table/index.
    for snippet in (
        "CREATE TABLE IF NOT EXISTS sensor_public_ids",
        "sensor_uuid TEXT NOT NULL UNIQUE",
        "idx_sensor_public_ids_sensor_uuid",
    ):
        if snippet not in schema:
            violations.append(f"{SCHEMA_FILE}: missing snippet `{snippet}`")

    # Routes contract.
    for snippet in (
        'app.router.add_get("/api/v1/public/sensors/status", public_sensors_status_handler)',
        'app.router.add_get("/api/v1/public/sensors/{sensor_id:\\\\d+}/status", public_sensor_status_handler)',
    ):
        if snippet not in api:
            violations.append(f"{API_FILE}: missing public route `{snippet}`")

    # Helper contract: heartbeat-only status.
    helper = _extract_function_body(api, "_sensor_is_online_by_heartbeat_only")
    if not helper:
        violations.append(f"{API_FILE}: missing _sensor_is_online_by_heartbeat_only()")
    else:
        if "last_heartbeat" not in helper:
            violations.append("heartbeat-only helper must use last_heartbeat")
        if "CFG.sensor_timeout" not in helper:
            violations.append("heartbeat-only helper must use CFG.sensor_timeout")
        if "frozen_" in helper:
            violations.append("heartbeat-only helper must not depend on freeze fields")
        if "is_sensor_online" in helper:
            violations.append("heartbeat-only helper must not call freeze-aware is_sensor_online")

    # Public handlers must validate public key and use heartbeat-only helper.
    for func_name in ("public_sensors_status_handler", "public_sensor_status_handler"):
        body = _extract_function_body(api, func_name)
        if not body:
            violations.append(f"{API_FILE}: missing {func_name}()")
            continue
        if "_validate_public_sensor_api_key(request)" not in body:
            violations.append(f"{func_name} must validate SENSOR_PUBLIC_API_KEY")
        if "_sensor_is_online_by_heartbeat_only(" not in body:
            violations.append(f"{func_name} must compute status via heartbeat-only helper")

    if violations:
        raise SystemExit(
            "ERROR: public sensor API policy violation(s):\n"
            + "\n".join(f"- {item}" for item in violations)
        )

    print("OK: public sensor API policy smoke passed.")


if __name__ == "__main__":
    main()
