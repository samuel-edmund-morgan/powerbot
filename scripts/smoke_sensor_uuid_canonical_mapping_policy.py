#!/usr/bin/env python3
"""
Static smoke-check: canonical sensor UUID -> building mapping policy.

Policy:
- Config must define DEFAULT_SENSOR_UUID_BUILDING_MAP with rollout UUIDs.
- CFG must expose `sensor_uuid_building_map`.
- Heartbeat handler must apply canonical mapping from CFG before DB upsert.

Run:
  python3 scripts/smoke_sensor_uuid_canonical_mapping_policy.py
"""

from __future__ import annotations

from pathlib import Path


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


REQUIRED_UUIDS = (
    "esp32-newcastle-001",
    "esp32-bristol-001",
    "esp32-liverpool-001",
    "esp32-nottingham-001",
    "esp32-manchester-001",
    "esp32-cambridge-001",
    "esp32-brighton-001",
    "esp32-birmingham-001",
    "esp32-windsor-001",
    "esp32-chester-001",
    "esp32-london-001",
    "esp32-oxford-001",
    "esp32-lincoln-001",
    "esp32-preston-001",
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> None:
    _assert(CONFIG_FILE.exists(), f"file not found: {CONFIG_FILE}")
    _assert(API_FILE.exists(), f"file not found: {API_FILE}")

    cfg = CONFIG_FILE.read_text(encoding="utf-8")
    api = API_FILE.read_text(encoding="utf-8")

    violations: list[str] = []

    # Config contract: default map + parser + CFG field.
    for snippet in (
        "DEFAULT_SENSOR_UUID_BUILDING_MAP",
        "parse_sensor_uuid_building_map_from_env(",
        "sensor_uuid_building_map: dict[str, int]",
        "sensor_uuid_building_map=parse_sensor_uuid_building_map_from_env(DEFAULT_SENSOR_UUID_BUILDING_MAP)",
    ):
        if snippet not in cfg:
            violations.append(f"{CONFIG_FILE}: missing snippet `{snippet}`")

    for uuid in REQUIRED_UUIDS:
        if f'"{uuid}"' not in cfg:
            violations.append(f"{CONFIG_FILE}: missing rollout uuid mapping `{uuid}`")

    # API contract: heartbeat must apply canonical mapping and then upsert.
    for snippet in (
        "canonical_building_id = CFG.sensor_uuid_building_map.get(sensor_uuid_key)",
        "canonical mapping applied",
        "upsert_sensor_heartbeat(sensor_uuid, building_id, section_id, sensor_name, comment)",
    ):
        if snippet not in api:
            violations.append(f"{API_FILE}: missing snippet `{snippet}`")

    if violations:
        raise SystemExit(
            "ERROR: sensor UUID canonical mapping policy violation(s):\n"
            + "\n".join(f"- {item}" for item in violations)
        )

    print("OK: sensor UUID canonical mapping policy smoke passed.")


if __name__ == "__main__":
    main()

