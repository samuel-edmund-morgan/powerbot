"""Centralized logging setup with optional rotating file persistence."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)s:%(name)s:%(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_DIR = "/data/logs"
DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 10


def _clean_env_value(value: str | None, default: str) -> str:
    if value is None:
        return default
    return value.strip().strip('"').strip("'") or default


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    cleaned = value.strip().strip('"').strip("'")
    if not cleaned:
        return default
    try:
        return int(cleaned)
    except ValueError:
        return default


def configure_logging(service_name: str) -> None:
    """Configure root logger for console + rotating file output."""
    level_name = _clean_env_value(os.getenv("LOG_LEVEL"), DEFAULT_LOG_LEVEL).upper()
    level = getattr(logging, level_name, logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler()]

    log_dir = _clean_env_value(os.getenv("LOG_DIR"), DEFAULT_LOG_DIR)
    log_file_name = _clean_env_value(os.getenv("LOG_FILE_NAME"), f"{service_name}.log")
    max_bytes = _parse_int(os.getenv("LOG_MAX_BYTES"), DEFAULT_LOG_MAX_BYTES)
    backup_count = _parse_int(os.getenv("LOG_BACKUP_COUNT"), DEFAULT_LOG_BACKUP_COUNT)

    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        file_path = Path(log_dir) / log_file_name
        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handlers.append(file_handler)
    except Exception as error:
        # Keep service alive even if file logging target is unavailable.
        logging.basicConfig(level=level, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT, force=True)
        logging.getLogger(__name__).warning(
            "File logging disabled: failed to initialize %s (%s)",
            log_dir,
            error,
        )
        return

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    for handler in handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
