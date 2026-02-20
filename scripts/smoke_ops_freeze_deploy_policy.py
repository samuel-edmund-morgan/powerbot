#!/usr/bin/env python3
"""
Static smoke-check: deploy freeze/unfreeze policy for sensors.

Policy:
- `scripts/deploy_prod.sh` must not force `light_notifications_global=off`.
- Prod deploy script must freeze active sensors before restart and unfreeze only
  deploy-frozen sensors (`frozen_at=FREEZE_AT`) after stack is up.
- Admin bot must expose bulk freeze/unfreeze actions via admin_jobs queue.
- Admin jobs worker must support `sensors_freeze_all` / `sensors_unfreeze_all`.
"""

from __future__ import annotations

from pathlib import Path


def _resolve(path_rel: str) -> Path:
    candidates: list[Path] = []
    try:
        candidates.append(Path(__file__).resolve().parents[1] / path_rel)
    except Exception:
        pass
    candidates.extend([Path.cwd() / path_rel, Path("/app") / path_rel])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else Path(path_rel)


DEPLOY_PROD = _resolve("scripts/deploy_prod.sh")
ADMIN_HANDLERS = _resolve("src/admin/handlers.py")
ADMIN_WORKER = _resolve("src/admin_jobs_worker.py")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _must(text: str, token: str, *, file_label: str, errors: list[str]) -> None:
    if token not in text:
        errors.append(f"{file_label}: missing token `{token}`")


def main() -> None:
    _assert(DEPLOY_PROD.exists(), f"file not found: {DEPLOY_PROD}")
    _assert(ADMIN_HANDLERS.exists(), f"file not found: {ADMIN_HANDLERS}")
    _assert(ADMIN_WORKER.exists(), f"file not found: {ADMIN_WORKER}")

    deploy_text = DEPLOY_PROD.read_text(encoding="utf-8")
    admin_handlers_text = ADMIN_HANDLERS.read_text(encoding="utf-8")
    admin_worker_text = ADMIN_WORKER.read_text(encoding="utf-8")
    errors: list[str] = []

    # deploy_prod contract: no global off, freeze/unfreeze by deploy marker.
    _must(
        deploy_text,
        "NOTE: deploy_prod no longer forces light_notifications_global=off.",
        file_label="scripts/deploy_prod.sh",
        errors=errors,
    )
    _must(
        deploy_text,
        "DEPLOY_FREEZE_SENSORS",
        file_label="scripts/deploy_prod.sh",
        errors=errors,
    )
    _must(
        deploy_text,
        "SET frozen_until=",
        file_label="scripts/deploy_prod.sh",
        errors=errors,
    )
    _must(
        deploy_text,
        "frozen_at='${FREEZE_AT}'",
        file_label="scripts/deploy_prod.sh",
        errors=errors,
    )
    _must(
        deploy_text,
        "Unfreezing deploy-frozen sensors",
        file_label="scripts/deploy_prod.sh",
        errors=errors,
    )
    _must(
        deploy_text,
        "SET frozen_until=NULL,",
        file_label="scripts/deploy_prod.sh",
        errors=errors,
    )
    _must(
        deploy_text,
        "WHERE frozen_at='${FREEZE_AT}';",
        file_label="scripts/deploy_prod.sh",
        errors=errors,
    )
    # Guard against re-introducing legacy forced global light OFF in deploy.
    if "UPDATE kv" in deploy_text and "light_notifications_global" in deploy_text:
        errors.append(
            "scripts/deploy_prod.sh: detected kv update for light_notifications_global (must rely on sensor freeze)"
        )

    # admin UI contract: bulk freeze/unfreeze available and queued.
    for token in (
        "admin_sensors_freeze_all|",
        "admin_sensors_unfreeze_all",
        "🧊 Всі до розморозки",
        "✅ Розморозити всі",
        'create_admin_job(\n        "sensors_freeze_all"',
        'create_admin_job(\n        "sensors_unfreeze_all"',
    ):
        _must(
            admin_handlers_text,
            token,
            file_label="src/admin/handlers.py",
            errors=errors,
        )

    # worker contract: job kinds and handlers exist.
    for token in (
        'JOB_KIND_SENSORS_FREEZE_ALL = "sensors_freeze_all"',
        'JOB_KIND_SENSORS_UNFREEZE_ALL = "sensors_unfreeze_all"',
        "async def _handle_sensors_freeze_all(",
        "async def _handle_sensors_unfreeze_all(",
        "elif kind == JOB_KIND_SENSORS_FREEZE_ALL:",
        "elif kind == JOB_KIND_SENSORS_UNFREEZE_ALL:",
    ):
        _must(
            admin_worker_text,
            token,
            file_label="src/admin_jobs_worker.py",
            errors=errors,
        )

    if errors:
        raise SystemExit(
            "ERROR: ops freeze deploy policy violation(s):\n"
            + "\n".join(f"- {err}" for err in errors)
        )

    print("OK: ops freeze deploy policy smoke passed.")


if __name__ == "__main__":
    main()
