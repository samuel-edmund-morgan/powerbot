#!/usr/bin/env python3
"""
Static smoke-check for business gallery 0..N media contract.

Policy:
- schema defines `place_gallery_media`;
- DB init creates table/index and exposes `get_place_gallery_media`;
- business repository/service expose list/add/remove operations;
- business bot UI has gallery callbacks.
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


def _must(text: str, token: str, *, file_label: str, errors: list[str]) -> None:
    if token not in text:
        errors.append(f"{file_label}: missing token `{token}`")


def main() -> None:
    schema = _resolve("schema.sql")
    database_py = _resolve("src/database.py")
    repo_py = _resolve("src/business/repository.py")
    service_py = _resolve("src/business/service.py")
    handlers_py = _resolve("src/business/handlers.py")

    errors: list[str] = []
    schema_text = schema.read_text(encoding="utf-8")
    db_text = database_py.read_text(encoding="utf-8")
    repo_text = repo_py.read_text(encoding="utf-8")
    service_text = service_py.read_text(encoding="utf-8")
    handlers_text = handlers_py.read_text(encoding="utf-8")

    for token in (
        "CREATE TABLE IF NOT EXISTS place_gallery_media",
        "idx_place_gallery_media_place_pos",
    ):
        _must(schema_text, token, file_label="schema.sql", errors=errors)

    for token in (
        "CREATE TABLE IF NOT EXISTS place_gallery_media",
        "idx_place_gallery_media_place_pos",
        "async def get_place_gallery_media(",
    ):
        _must(db_text, token, file_label="src/database.py", errors=errors)

    for token in (
        "async def list_place_gallery_media(",
        "async def count_place_gallery_media(",
        "async def add_place_gallery_media(",
        "async def remove_place_gallery_media(",
    ):
        _must(repo_text, token, file_label="src/business/repository.py", errors=errors)

    for token in (
        "def _gallery_limit_for_tier(",
        "async def list_place_gallery_media(",
        "async def add_place_gallery_media(",
        "async def remove_place_gallery_media(",
    ):
        _must(service_text, token, file_label="src/business/service.py", errors=errors)

    for token in (
        'CB_GALLERY_MENU_PREFIX = "begal:"',
        'CB_GALLERY_ADD_PREFIX = "begal_add:"',
        'CB_GALLERY_DEL_PREFIX = "begal_del:"',
        "async def cb_gallery_menu(",
        "async def cb_gallery_add(",
        "async def cb_gallery_delete(",
        "EditPlaceStates.waiting_gallery_media",
    ):
        _must(handlers_text, token, file_label="src/business/handlers.py", errors=errors)

    if errors:
        raise SystemExit(
            "ERROR: business gallery policy violation(s):\n- " + "\n- ".join(errors)
        )

    print("OK: business gallery policy smoke passed.")


if __name__ == "__main__":
    main()

