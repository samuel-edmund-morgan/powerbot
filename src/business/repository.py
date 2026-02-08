"""Persistence helpers for business module."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

from config import DB_PATH

PRAGMA_BUSY_TIMEOUT_MS = 5000
WRITE_RETRY_ATTEMPTS = 3
WRITE_RETRY_BASE_DELAY_SEC = 0.05


async def apply_sqlite_pragmas(db: aiosqlite.Connection) -> None:
    """Apply pragmatic SQLite settings for concurrent bot processes."""
    await db.execute("PRAGMA journal_mode=WAL;")
    await db.execute("PRAGMA synchronous=NORMAL;")
    await db.execute(f"PRAGMA busy_timeout={PRAGMA_BUSY_TIMEOUT_MS};")


@asynccontextmanager
async def open_business_db() -> AsyncIterator[aiosqlite.Connection]:
    """Open connection with required PRAGMA settings."""
    async with aiosqlite.connect(DB_PATH) as db:
        await apply_sqlite_pragmas(db)
        yield db


async def execute_write_with_retry(
    db: aiosqlite.Connection,
    query: str,
    params: Sequence[Any] = (),
) -> aiosqlite.Cursor:
    """Execute write query with lightweight retry on lock contention."""
    last_error: Exception | None = None
    for attempt in range(WRITE_RETRY_ATTEMPTS):
        try:
            cursor = await db.execute(query, params)
            await db.commit()
            return cursor
        except aiosqlite.OperationalError as error:
            if "database is locked" not in str(error).lower():
                raise
            last_error = error
            if attempt < WRITE_RETRY_ATTEMPTS - 1:
                backoff = WRITE_RETRY_BASE_DELAY_SEC * (2**attempt)
                await asyncio.sleep(backoff)
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unexpected retry loop state")
