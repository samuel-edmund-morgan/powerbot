"""Persistence helpers for business module."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from config import DB_PATH
from database import build_keywords
from sqlite_lock_logger import log_sqlite_lock_event

PRAGMA_BUSY_TIMEOUT_MS = 5000
WRITE_RETRY_ATTEMPTS = 3
WRITE_RETRY_BASE_DELAY_SEC = 0.05
logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    """Current UTC timestamp in ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


async def apply_sqlite_pragmas(db: aiosqlite.Connection) -> None:
    """Apply pragmatic SQLite settings for concurrent bot processes."""
    await db.execute("PRAGMA journal_mode=WAL;")
    await db.execute("PRAGMA synchronous=NORMAL;")
    await db.execute(f"PRAGMA busy_timeout={PRAGMA_BUSY_TIMEOUT_MS};")


@asynccontextmanager
async def open_business_db() -> AsyncIterator[aiosqlite.Connection]:
    """Open connection with required PRAGMA settings."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
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
            delay = WRITE_RETRY_BASE_DELAY_SEC * (2**attempt)
            op = ""
            table = ""
            try:
                cleaned = " ".join(str(query or "").strip().split()).lower()
                # best-effort parse: "insert into <table>", "update <table>", "delete from <table>"
                for prefix in ("insert into ", "update ", "delete from "):
                    if cleaned.startswith(prefix):
                        rest = cleaned[len(prefix):].lstrip()
                        table = rest.split(" ", 1)[0].strip().strip('"')
                        op = prefix.strip()
                        break
            except Exception:
                op = ""
                table = ""
            logger.warning(
                "SQLite locked; retry %s/%s in %.2fs (%s %s)",
                attempt + 1,
                WRITE_RETRY_ATTEMPTS,
                delay,
                op or "write",
                table or "?",
            )
            log_sqlite_lock_event(
                where="business.execute_write_with_retry",
                exc=error,
                attempt=attempt + 1,
                retries=WRITE_RETRY_ATTEMPTS,
                delay_sec=delay,
                extra={"op": op or "write", "table": table or None},
            )
            if attempt < WRITE_RETRY_ATTEMPTS - 1:
                await asyncio.sleep(delay)
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unexpected retry loop state")


class BusinessRepository:
    """Business persistence and guard queries."""

    async def list_all_place_ids(self) -> list[int]:
        async with open_business_db() as db:
            async with db.execute("SELECT id FROM places ORDER BY id") as cur:
                rows = await cur.fetchall()
                return [int(row[0]) for row in rows]

    async def list_services_with_place_counts(self) -> list[dict[str, Any]]:
        async with open_business_db() as db:
            async with db.execute(
                """
                SELECT s.id, s.name, COUNT(p.id) AS place_count
                  FROM general_services s
                  JOIN places p ON p.service_id = s.id
                 GROUP BY s.id, s.name
                 ORDER BY s.name COLLATE NOCASE
                """
            ) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def list_services_with_place_counts_filtered(
        self,
        *,
        is_published: int | None,
    ) -> list[dict[str, Any]]:
        """List services with place counts using optional publish filter."""
        where_clause = ""
        params: tuple[Any, ...] = ()
        if is_published is not None:
            where_clause = "WHERE p.is_published = ?"
            params = (1 if int(is_published) else 0,)
        query = (
            """
            SELECT s.id, s.name, COUNT(p.id) AS place_count
              FROM general_services s
              JOIN places p ON p.service_id = s.id
            """
            + where_clause
            + """
             GROUP BY s.id, s.name
             ORDER BY s.name COLLATE NOCASE
            """
        )
        async with open_business_db() as db:
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def count_places_by_service(self, service_id: int) -> int:
        async with open_business_db() as db:
            async with db.execute(
                "SELECT COUNT(*) AS cnt FROM places WHERE service_id = ?",
                (int(service_id),),
            ) as cur:
                row = await cur.fetchone()
                return int(row[0] if row else 0)

    async def count_places_by_service_filtered(
        self,
        service_id: int,
        *,
        is_published: int | None,
    ) -> int:
        query = "SELECT COUNT(*) AS cnt FROM places WHERE service_id = ?"
        params: list[Any] = [int(service_id)]
        if is_published is not None:
            query += " AND is_published = ?"
            params.append(1 if int(is_published) else 0)
        async with open_business_db() as db:
            async with db.execute(query, tuple(params)) as cur:
                row = await cur.fetchone()
                return int(row[0] if row else 0)

    async def list_places_by_service(
        self,
        service_id: int,
        *,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 50))
        safe_offset = max(0, int(offset))
        async with open_business_db() as db:
            async with db.execute(
                """
                SELECT id, name, address
                  FROM places
                 WHERE service_id = ?
                 ORDER BY name COLLATE NOCASE
                 LIMIT ? OFFSET ?
                """,
                (int(service_id), safe_limit, safe_offset),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def list_places_by_service_filtered(
        self,
        service_id: int,
        *,
        is_published: int | None,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 50))
        safe_offset = max(0, int(offset))
        query = (
            "SELECT id, name, address, is_published "
            "  FROM places "
            " WHERE service_id = ?"
        )
        params: list[Any] = [int(service_id)]
        if is_published is not None:
            query += " AND is_published = ?"
            params.append(1 if int(is_published) else 0)
        query += " ORDER BY name COLLATE NOCASE LIMIT ? OFFSET ?"
        params.extend([safe_limit, safe_offset])
        async with open_business_db() as db:
            async with db.execute(query, tuple(params)) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def search_places_filtered(
        self,
        query: str,
        *,
        is_published: int | None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search places for admin UI by id or name/address within optional publish filter."""
        q = str(query or "").strip()
        if not q:
            return []

        safe_limit = max(1, min(int(limit), 50))
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{esc}%"

        where_parts: list[str] = []
        params: list[Any] = []

        if is_published is not None:
            where_parts.append("p.is_published = ?")
            params.append(1 if int(is_published) else 0)

        order_prefix = ""
        if q.isdigit():
            where_parts.append(
                "(p.id = ? OR p.name LIKE ? ESCAPE '\\' COLLATE NOCASE OR p.address LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            pid = int(q)
            params.extend([pid, like, like])
            order_prefix = "ORDER BY (p.id = ?) DESC, p.name COLLATE NOCASE, p.id DESC "
            order_params: list[Any] = [pid]
        else:
            where_parts.append("(p.name LIKE ? ESCAPE '\\' COLLATE NOCASE OR p.address LIKE ? ESCAPE '\\' COLLATE NOCASE)")
            params.extend([like, like])
            order_params = []

        where_sql = " AND ".join(where_parts) if where_parts else "1=1"
        sql = (
            "SELECT p.id, p.service_id, p.name, p.address, p.is_published, s.name AS service_name "
            "  FROM places p "
            "  LEFT JOIN general_services s ON s.id = p.service_id "
            f" WHERE {where_sql} "
        )
        if order_prefix:
            sql += order_prefix
            params.extend(order_params)
        else:
            sql += "ORDER BY p.name COLLATE NOCASE, p.id DESC "
        sql += "LIMIT ?"
        params.append(safe_limit)

        async with open_business_db() as db:
            async with db.execute(sql, tuple(params)) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def list_place_ids_missing_active_claim_token(self, *, now_iso: str) -> list[int]:
        """Return place ids that have no active (non-expired) claim token."""
        async with open_business_db() as db:
            async with db.execute(
                """
                SELECT p.id
                  FROM places p
                 WHERE NOT EXISTS (
                       SELECT 1
                         FROM business_claim_tokens t
                        WHERE t.place_id = p.id
                          AND t.status = 'active'
                          AND t.expires_at > ?
                 )
                 ORDER BY p.id
                """,
                (str(now_iso),),
            ) as cur:
                rows = await cur.fetchall()
                return [int(r[0]) for r in rows]

    async def get_active_claim_token_for_place(self, place_id: int, *, now_iso: str) -> dict[str, Any] | None:
        async with open_business_db() as db:
            async with db.execute(
                """
                SELECT id, place_id, token, status, attempts_left,
                       created_at, expires_at, created_by, used_at, used_by
                  FROM business_claim_tokens
                 WHERE place_id = ?
                   AND status = 'active'
                   AND expires_at > ?
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                (int(place_id), str(now_iso)),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def revoke_active_claim_tokens_for_place(self, place_id: int) -> int:
        """Revoke all active claim tokens for the given place. Returns affected rows."""
        now = utc_now_iso()
        async with open_business_db() as db:
            cursor = await execute_write_with_retry(
                db,
                """
                UPDATE business_claim_tokens
                   SET status = 'revoked',
                       used_at = ?,
                       used_by = NULL
                 WHERE place_id = ?
                   AND status = 'active'
                """,
                (now, int(place_id)),
            )
            return int(cursor.rowcount or 0)

    async def rotate_claim_tokens_for_places(
        self,
        place_ids: Sequence[int],
        tokens: Sequence[str],
        *,
        created_by: int | None,
        expires_at: str,
        attempts_left: int = 5,
    ) -> None:
        """Atomically revoke active tokens and insert new ones for multiple places.

        This is used by admin bulk rotation actions. The operation runs in a single
        transaction so we never end up with "revoked but not inserted" state if
        an error happens mid-way.
        """
        if not place_ids:
            return
        if len(place_ids) != len(tokens):
            raise ValueError("place_ids and tokens must have the same length")
        if attempts_left < 1:
            raise ValueError("attempts_left must be >= 1")

        created_at = utc_now_iso()
        id_list = [int(pid) for pid in place_ids]
        placeholders = ",".join("?" for _ in id_list)
        update_query = (
            "UPDATE business_claim_tokens "
            "   SET status = 'revoked', "
            "       used_at = ?, "
            "       used_by = NULL "
            f" WHERE place_id IN ({placeholders}) "
            "   AND status = 'active'"
        )
        insert_query = (
            "INSERT INTO business_claim_tokens("
            "  place_id, token, status, attempts_left, "
            "  created_at, expires_at, created_by, used_at, used_by"
            ") VALUES(?, ?, 'active', ?, ?, ?, ?, NULL, NULL)"
        )
        values = [
            (int(pid), str(token), int(attempts_left), created_at, str(expires_at), created_by)
            for pid, token in zip(id_list, tokens, strict=True)
        ]

        last_error: Exception | None = None
        for attempt in range(WRITE_RETRY_ATTEMPTS):
            try:
                async with open_business_db() as db:
                    await db.execute("BEGIN")
                    await db.execute(update_query, (created_at, *id_list))
                    await db.executemany(insert_query, values)
                    await db.commit()
                return
            except aiosqlite.OperationalError as error:
                # If the database is locked, retry the whole transaction.
                if "database is locked" not in str(error).lower():
                    raise
                last_error = error
                delay = WRITE_RETRY_BASE_DELAY_SEC * (2**attempt)
                logger.warning("SQLite locked; retry %s/%s in %.2fs (bulk_rotate_claim_tokens)", attempt + 1, WRITE_RETRY_ATTEMPTS, delay)
                log_sqlite_lock_event(
                    where="business.bulk_rotate_claim_tokens",
                    exc=error,
                    attempt=attempt + 1,
                    retries=WRITE_RETRY_ATTEMPTS,
                    delay_sec=delay,
                )
                await asyncio.sleep(delay)
                continue
            except Exception as error:
                last_error = error
                raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("Unexpected retry loop state")

    async def get_building(self, building_id: int) -> dict[str, Any] | None:
        async with open_business_db() as db:
            async with db.execute(
                "SELECT id, name, address FROM buildings WHERE id = ?",
                (int(building_id),),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def list_buildings(self) -> list[dict[str, Any]]:
        async with open_business_db() as db:
            async with db.execute(
                "SELECT id, name, address FROM buildings ORDER BY id",
            ) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def get_service(self, service_id: int) -> dict[str, Any] | None:
        async with open_business_db() as db:
            async with db.execute(
                "SELECT id, name FROM general_services WHERE id = ?",
                (int(service_id),),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def list_services(self) -> list[dict[str, Any]]:
        async with open_business_db() as db:
            async with db.execute(
                "SELECT id, name FROM general_services ORDER BY name COLLATE NOCASE",
            ) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def create_service_if_missing(self, service_name: str) -> dict[str, Any]:
        """Create category if missing (case-insensitive)."""
        clean_name = str(service_name or "").strip()
        if not clean_name:
            raise ValueError("service_name is required")

        async with open_business_db() as db:
            async with db.execute(
                "SELECT id, name FROM general_services WHERE lower(name) = lower(?)",
                (clean_name,),
            ) as cur:
                row = await cur.fetchone()
                if row:
                    return {"id": int(row["id"]), "name": str(row["name"]), "created": False}

            cursor = await execute_write_with_retry(
                db,
                "INSERT INTO general_services(name) VALUES(?)",
                (clean_name,),
            )
            service_id = int(cursor.lastrowid)
            return {"id": service_id, "name": clean_name, "created": True}

    async def rename_service(self, service_id: int, service_name: str) -> bool:
        clean_name = str(service_name or "").strip()
        if not clean_name:
            raise ValueError("service_name is required")
        async with open_business_db() as db:
            cursor = await execute_write_with_retry(
                db,
                "UPDATE general_services SET name=? WHERE id=?",
                (clean_name, int(service_id)),
            )
            return int(cursor.rowcount or 0) > 0

    async def get_place(self, place_id: int) -> dict[str, Any] | None:
        async with open_business_db() as db:
            async with db.execute(
                """SELECT p.id, p.service_id, p.name, p.description, p.address, p.keywords,
                          p.is_published,
                          p.is_verified, p.verified_tier, p.verified_until, p.business_enabled,
                          s.name AS service_name
                     FROM places p
                     JOIN general_services s ON s.id = p.service_id
                    WHERE p.id = ?""",
                (place_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def delete_place_draft(self, place_id: int) -> bool:
        """Delete an unpublished place and its related business rows.

        Safety: only deletes when is_published=0 to avoid accidental removal of a live catalog entry.
        """
        pid = int(place_id)
        last_error: Exception | None = None
        for attempt in range(WRITE_RETRY_ATTEMPTS):
            try:
                async with open_business_db() as db:
                    async with db.execute(
                        "SELECT is_published FROM places WHERE id = ?",
                        (pid,),
                    ) as cur:
                        row = await cur.fetchone()
                        if not row:
                            return False
                        if int(row["is_published"] or 0) != 0:
                            return False

                    await db.execute("BEGIN")
                    # Business-related tables
                    await db.execute("DELETE FROM business_owners WHERE place_id = ?", (pid,))
                    await db.execute("DELETE FROM business_subscriptions WHERE place_id = ?", (pid,))
                    await db.execute("DELETE FROM business_claim_tokens WHERE place_id = ?", (pid,))
                    await db.execute("DELETE FROM business_payment_events WHERE place_id = ?", (pid,))
                    # Legacy likes table (may be empty for drafts, but keep DB tidy)
                    await db.execute("DELETE FROM place_likes WHERE place_id = ?", (pid,))
                    cursor = await db.execute("DELETE FROM places WHERE id = ?", (pid,))
                    await db.commit()
                    return int(cursor.rowcount or 0) > 0
            except aiosqlite.OperationalError as error:
                if "database is locked" not in str(error).lower():
                    raise
                last_error = error
                delay = WRITE_RETRY_BASE_DELAY_SEC * (2**attempt)
                logger.warning("SQLite locked; retry %s/%s in %.2fs (delete_place_draft)", attempt + 1, WRITE_RETRY_ATTEMPTS, delay)
                log_sqlite_lock_event(
                    where="business.delete_place_draft",
                    exc=error,
                    attempt=attempt + 1,
                    retries=WRITE_RETRY_ATTEMPTS,
                    delay_sec=delay,
                    extra={"place_id": pid},
                )
                await asyncio.sleep(delay)
                continue
            except Exception as error:
                last_error = error
                raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("Unexpected retry loop state")

    async def get_places_business_meta(self, place_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
        """Batch load business metadata for places.

        Keep this read-only and fast: it's used by main bot/webapp integration.
        """
        ids: list[int] = []
        seen: set[int] = set()
        for raw in place_ids:
            try:
                pid = int(raw)
            except Exception:
                continue
            if pid in seen:
                continue
            seen.add(pid)
            ids.append(pid)

        if not ids:
            return {}

        # SQLite variable limit is usually 999; keep a safe margin.
        chunk_size = 900
        result: dict[int, dict[str, Any]] = {}
        async with open_business_db() as db:
            for i in range(0, len(ids), chunk_size):
                chunk = ids[i : i + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                query = (
                    "SELECT id, business_enabled, is_verified, verified_tier, verified_until "
                    f"FROM places WHERE id IN ({placeholders})"
                )
                async with db.execute(query, chunk) as cur:
                    rows = await cur.fetchall()
                    for row in rows:
                        result[int(row["id"])] = dict(row)
        return result

    async def get_or_create_service_id(self, service_name: str) -> int:
        async with open_business_db() as db:
            async with db.execute(
                "SELECT id FROM general_services WHERE lower(name) = lower(?)",
                (service_name,),
            ) as cur:
                row = await cur.fetchone()
                if row:
                    return int(row["id"])

            cursor = await execute_write_with_retry(
                db,
                "INSERT INTO general_services(name) VALUES(?)",
                (service_name,),
            )
            return int(cursor.lastrowid)

    async def create_place(
        self,
        service_id: int,
        name: str,
        description: str,
        address: str,
    ) -> int:
        keywords = build_keywords(name, description, None)
        async with open_business_db() as db:
            cursor = await execute_write_with_retry(
                db,
                """INSERT INTO places(
                       service_id, name, description, address, keywords,
                       is_published,
                       is_verified, verified_tier, verified_until, business_enabled
                   ) VALUES(?, ?, ?, ?, ?, 0, 0, NULL, NULL, 0)""",
                (service_id, name, description, address, keywords),
            )
            return int(cursor.lastrowid)

    async def set_place_published(self, place_id: int, *, is_published: int) -> None:
        """Toggle whether a place is visible in the resident catalog."""
        async with open_business_db() as db:
            await execute_write_with_retry(
                db,
                "UPDATE places SET is_published=? WHERE id=?",
                (1 if int(is_published) else 0, int(place_id)),
            )

    async def upsert_owner_request(
        self,
        place_id: int,
        tg_user_id: int,
        role: str = "owner",
    ) -> dict[str, Any]:
        created_at = utc_now_iso()
        async with open_business_db() as db:
            await execute_write_with_retry(
                db,
                """INSERT INTO business_owners(
                       place_id, tg_user_id, role, status, created_at, approved_at, approved_by
                   ) VALUES(?, ?, ?, 'pending', ?, NULL, NULL)
                   ON CONFLICT(place_id, tg_user_id) DO UPDATE SET
                     role = excluded.role,
                     status = CASE
                       WHEN business_owners.status = 'approved' THEN 'approved'
                       ELSE 'pending'
                     END,
                     created_at = CASE
                       WHEN business_owners.status = 'approved' THEN business_owners.created_at
                       ELSE excluded.created_at
                     END,
                     approved_at = CASE
                       WHEN business_owners.status = 'approved' THEN business_owners.approved_at
                       ELSE NULL
                     END,
                     approved_by = CASE
                       WHEN business_owners.status = 'approved' THEN business_owners.approved_by
                       ELSE NULL
                     END""",
                (place_id, tg_user_id, role, created_at),
            )
            async with db.execute(
                """SELECT id, place_id, tg_user_id, role, status, created_at, approved_at, approved_by
                     FROM business_owners
                    WHERE place_id = ? AND tg_user_id = ?""",
                (place_id, tg_user_id),
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    raise RuntimeError("Failed to read owner request after upsert")
                return dict(row)

    async def get_owner_request(self, owner_id: int) -> dict[str, Any] | None:
        async with open_business_db() as db:
            async with db.execute(
                """SELECT bo.id, bo.place_id, bo.tg_user_id, bo.role, bo.status,
                          bo.created_at, bo.approved_at, bo.approved_by,
                          p.name AS place_name
                     FROM business_owners bo
                     JOIN places p ON p.id = bo.place_id
                    WHERE bo.id = ?""",
                (owner_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def list_pending_owner_requests(self, limit: int = 50) -> list[dict[str, Any]]:
        async with open_business_db() as db:
            async with db.execute(
                """SELECT bo.id AS owner_id, bo.place_id, bo.tg_user_id, bo.role, bo.status,
                          bo.created_at, p.name AS place_name, p.address AS place_address,
                          s.username, s.first_name
                     FROM business_owners bo
                     JOIN places p ON p.id = bo.place_id
                     LEFT JOIN subscribers s ON s.chat_id = bo.tg_user_id
                    WHERE bo.status = 'pending'
                    ORDER BY bo.created_at ASC
                    LIMIT ?""",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def get_pending_owner_request_for_place(self, place_id: int) -> dict[str, Any] | None:
        async with open_business_db() as db:
            async with db.execute(
                """SELECT bo.id AS owner_id, bo.place_id, bo.tg_user_id, bo.role, bo.status,
                          bo.created_at, p.name AS place_name, p.address AS place_address,
                          s.username, s.first_name
                     FROM business_owners bo
                     JOIN places p ON p.id = bo.place_id
                     LEFT JOIN subscribers s ON s.chat_id = bo.tg_user_id
                    WHERE bo.place_id = ? AND bo.status = 'pending'
                    ORDER BY bo.created_at ASC
                    LIMIT 1""",
                (int(place_id),),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def update_owner_status(
        self,
        owner_id: int,
        status: str,
        reviewed_by: int,
    ) -> dict[str, Any] | None:
        existing = await self.get_owner_request(owner_id)
        if not existing:
            return None
        reviewed_at = utc_now_iso()
        async with open_business_db() as db:
            await execute_write_with_retry(
                db,
                """UPDATE business_owners
                      SET status = ?, approved_at = ?, approved_by = ?
                    WHERE id = ?""",
                (status, reviewed_at, reviewed_by, owner_id),
            )
            async with db.execute(
                """SELECT bo.id, bo.place_id, bo.tg_user_id, bo.role, bo.status,
                          bo.created_at, bo.approved_at, bo.approved_by,
                          p.name AS place_name
                     FROM business_owners bo
                     JOIN places p ON p.id = bo.place_id
                    WHERE bo.id = ?""",
                (owner_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def has_approved_owners(self, place_id: int) -> bool:
        async with open_business_db() as db:
            async with db.execute(
                "SELECT 1 FROM business_owners WHERE place_id = ? AND status = 'approved' LIMIT 1",
                (place_id,),
            ) as cur:
                return await cur.fetchone() is not None

    async def ensure_subscription(self, place_id: int) -> dict[str, Any]:
        now = utc_now_iso()
        async with open_business_db() as db:
            await execute_write_with_retry(
                db,
                """INSERT INTO business_subscriptions(
                       place_id, tier, status, starts_at, expires_at, created_at, updated_at
                   ) VALUES(?, 'free', 'inactive', NULL, NULL, ?, ?)
                   ON CONFLICT(place_id) DO NOTHING""",
                (place_id, now, now),
            )
            async with db.execute(
                """SELECT place_id, tier, status, starts_at, expires_at, created_at, updated_at
                     FROM business_subscriptions
                    WHERE place_id = ?""",
                (place_id,),
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    raise RuntimeError("Failed to read subscription")
                return dict(row)

    async def list_subscriptions_for_reconcile(
        self,
        *,
        limit: int,
        after_place_id: int = 0,
    ) -> list[dict[str, Any]]:
        """List subscriptions in stable place_id order for lifecycle reconciliation."""
        safe_limit = max(1, min(int(limit), 200))
        cursor = max(0, int(after_place_id))
        async with open_business_db() as db:
            async with db.execute(
                """
                SELECT place_id, tier, status, starts_at, expires_at, created_at, updated_at
                  FROM business_subscriptions
                 WHERE place_id > ?
                 ORDER BY place_id ASC
                 LIMIT ?
                """,
                (cursor, safe_limit),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def update_subscription(
        self,
        place_id: int,
        tier: str,
        status: str,
        starts_at: str | None,
        expires_at: str | None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        async with open_business_db() as db:
            await execute_write_with_retry(
                db,
                """INSERT INTO business_subscriptions(
                       place_id, tier, status, starts_at, expires_at, created_at, updated_at
                   ) VALUES(?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(place_id) DO UPDATE SET
                       tier = excluded.tier,
                       status = excluded.status,
                       starts_at = excluded.starts_at,
                       expires_at = excluded.expires_at,
                       updated_at = excluded.updated_at""",
                (place_id, tier, status, starts_at, expires_at, now, now),
            )
            async with db.execute(
                """SELECT place_id, tier, status, starts_at, expires_at, created_at, updated_at
                     FROM business_subscriptions
                    WHERE place_id = ?""",
                (place_id,),
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    raise RuntimeError("Failed to update subscription")
                return dict(row)

    async def update_place_business_flags(
        self,
        place_id: int,
        *,
        business_enabled: int,
        is_verified: int,
        verified_tier: str | None,
        verified_until: str | None,
    ) -> None:
        async with open_business_db() as db:
            await execute_write_with_retry(
                db,
                """UPDATE places
                      SET business_enabled = ?,
                          is_verified = ?,
                          verified_tier = ?,
                          verified_until = ?
                    WHERE id = ?""",
                (business_enabled, is_verified, verified_tier, verified_until, place_id),
            )

    async def list_user_businesses(self, tg_user_id: int) -> list[dict[str, Any]]:
        async with open_business_db() as db:
            async with db.execute(
                """SELECT bo.id AS owner_id, bo.place_id, bo.role, bo.status AS ownership_status,
                          bo.created_at AS ownership_created_at,
                          p.name AS place_name, p.description AS place_description, p.address AS place_address,
                          p.business_enabled, p.is_verified, p.verified_tier, p.verified_until,
                          COALESCE(bs.tier, 'free') AS tier,
                          COALESCE(bs.status, 'inactive') AS subscription_status,
                          bs.starts_at AS subscription_starts_at,
                          bs.expires_at AS subscription_expires_at
                     FROM business_owners bo
                     JOIN places p ON p.id = bo.place_id
                     LEFT JOIN business_subscriptions bs ON bs.place_id = bo.place_id
                    WHERE bo.tg_user_id = ?
                    ORDER BY
                      CASE bo.status
                        WHEN 'approved' THEN 0
                        WHEN 'pending' THEN 1
                        ELSE 2
                      END,
                      p.name ASC""",
                (tg_user_id,),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def is_approved_owner(self, tg_user_id: int, place_id: int) -> bool:
        async with open_business_db() as db:
            async with db.execute(
                """SELECT 1
                     FROM business_owners
                    WHERE tg_user_id = ? AND place_id = ? AND status = 'approved'
                    LIMIT 1""",
                (tg_user_id, place_id),
            ) as cur:
                return await cur.fetchone() is not None

    async def update_place_profile_field(
        self,
        place_id: int,
        field: str,
        value: str,
    ) -> dict[str, Any] | None:
        if field not in {"name", "description", "address"}:
            raise ValueError("Unsupported field")
        async with open_business_db() as db:
            async with db.execute(
                "SELECT id, name, description, address, keywords FROM places WHERE id = ?",
                (place_id,),
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return None
                place = dict(row)

            updated_name = place["name"]
            updated_description = place["description"] or ""
            updated_address = place["address"] or ""
            if field == "name":
                updated_name = value
            elif field == "description":
                updated_description = value
            else:
                updated_address = value
            merged_keywords = build_keywords(updated_name, updated_description, place["keywords"])

            await execute_write_with_retry(
                db,
                """UPDATE places
                      SET name = ?, description = ?, address = ?, keywords = ?
                    WHERE id = ?""",
                (updated_name, updated_description, updated_address, merged_keywords, place_id),
            )

            async with db.execute(
                """SELECT id, name, description, address, keywords,
                          business_enabled, is_verified, verified_tier, verified_until
                     FROM places
                    WHERE id = ?""",
                (place_id,),
            ) as cur:
                updated = await cur.fetchone()
                return dict(updated) if updated else None

    async def write_audit_log(
        self,
        place_id: int,
        actor_tg_user_id: int | None,
        action: str,
        payload_json: str | None,
    ) -> None:
        created_at = utc_now_iso()
        async with open_business_db() as db:
            await execute_write_with_retry(
                db,
                """INSERT INTO business_audit_log(
                       place_id, actor_tg_user_id, action, payload_json, created_at
                   ) VALUES(?, ?, ?, ?, ?)""",
                (place_id, actor_tg_user_id, action, payload_json, created_at),
            )

    async def write_audit_logs_bulk(
        self,
        rows: Sequence[tuple[int, int | None, str, str | None]],
    ) -> None:
        """Insert many audit rows in a single short write transaction."""
        if not rows:
            return
        created_at = utc_now_iso()
        last_error: Exception | None = None
        for attempt in range(WRITE_RETRY_ATTEMPTS):
            try:
                async with open_business_db() as db:
                    await db.execute("BEGIN")
                    await db.executemany(
                        """INSERT INTO business_audit_log(
                               place_id, actor_tg_user_id, action, payload_json, created_at
                           ) VALUES(?, ?, ?, ?, ?)""",
                        [
                            (
                                int(place_id),
                                int(actor_tg_user_id) if actor_tg_user_id is not None else None,
                                str(action),
                                payload_json,
                                created_at,
                            )
                            for (place_id, actor_tg_user_id, action, payload_json) in rows
                        ],
                    )
                    await db.commit()
                    return
            except aiosqlite.OperationalError as error:
                if "database is locked" not in str(error).lower():
                    raise
                last_error = error
                delay = WRITE_RETRY_BASE_DELAY_SEC * (2**attempt)
                logger.warning("SQLite locked; retry %s/%s in %.2fs (write_audit_logs_bulk)", attempt + 1, WRITE_RETRY_ATTEMPTS, delay)
                log_sqlite_lock_event(
                    where="business.write_audit_logs_bulk",
                    exc=error,
                    attempt=attempt + 1,
                    retries=WRITE_RETRY_ATTEMPTS,
                    delay_sec=delay,
                    extra={"rows": len(rows)},
                )
                await asyncio.sleep(delay)
                continue
            except Exception as error:
                last_error = error
                raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("Unexpected retry loop state")

    async def create_payment_event(
        self,
        *,
        place_id: int,
        provider: str,
        external_payment_id: str | None,
        event_type: str,
        amount_stars: int | None,
        currency: str = "XTR",
        status: str = "new",
        raw_payload_json: str | None = None,
        processed_at: str | None = None,
    ) -> bool:
        """Insert payment event idempotently.

        Returns True when a new row was inserted, False when ignored by unique index.
        """
        created_at = utc_now_iso()
        async with open_business_db() as db:
            cursor = await execute_write_with_retry(
                db,
                """INSERT OR IGNORE INTO business_payment_events(
                       place_id, provider, external_payment_id, event_type,
                       amount_stars, currency, status, raw_payload_json, created_at, processed_at
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(place_id),
                    str(provider),
                    str(external_payment_id) if external_payment_id else None,
                    str(event_type),
                    int(amount_stars) if amount_stars is not None else None,
                    str(currency or "XTR"),
                    str(status or "new"),
                    raw_payload_json,
                    created_at,
                    processed_at,
                ),
            )
            return int(cursor.rowcount or 0) > 0

    async def get_payment_events_by_external_id(
        self,
        *,
        provider: str,
        external_payment_id: str,
    ) -> list[dict[str, Any]]:
        async with open_business_db() as db:
            async with db.execute(
                """SELECT id, place_id, provider, external_payment_id, event_type,
                          amount_stars, currency, status, raw_payload_json, created_at, processed_at
                     FROM business_payment_events
                    WHERE provider = ? AND external_payment_id = ?
                    ORDER BY id ASC""",
                (str(provider), str(external_payment_id)),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def get_payment_event_admin_view(self, event_id: int) -> dict[str, Any] | None:
        """Load a single payment event with place info for admin UI."""
        async with open_business_db() as db:
            async with db.execute(
                """
                SELECT e.id, e.place_id, e.provider, e.external_payment_id, e.event_type,
                       e.amount_stars, e.currency, e.status, e.raw_payload_json, e.created_at, e.processed_at,
                       p.name AS place_name, p.address AS place_address
                  FROM business_payment_events e
                  JOIN places p ON p.id = e.place_id
                 WHERE e.id = ?
                 LIMIT 1
                """,
                (int(event_id),),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def count_all_business_payment_events(self) -> int:
        async with open_business_db() as db:
            async with db.execute("SELECT COUNT(*) FROM business_payment_events") as cur:
                row = await cur.fetchone()
                return int(row[0] if row else 0)

    async def list_all_business_payment_events(
        self,
        *,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, int(offset))
        async with open_business_db() as db:
            async with db.execute(
                """
                SELECT e.id, e.place_id, e.provider, e.external_payment_id, e.event_type,
                       e.amount_stars, e.currency, e.status, e.raw_payload_json, e.created_at, e.processed_at,
                       p.name AS place_name, p.address AS place_address,
                       (
                         SELECT bo.tg_user_id
                           FROM business_owners bo
                          WHERE bo.place_id = e.place_id
                          ORDER BY
                            CASE bo.status
                              WHEN 'approved' THEN 0
                              WHEN 'pending' THEN 1
                              WHEN 'rejected' THEN 2
                              ELSE 3
                            END,
                            bo.created_at DESC,
                            bo.id DESC
                          LIMIT 1
                       ) AS owner_tg_user_id,
                       (
                         SELECT bo.status
                           FROM business_owners bo
                          WHERE bo.place_id = e.place_id
                          ORDER BY
                            CASE bo.status
                              WHEN 'approved' THEN 0
                              WHEN 'pending' THEN 1
                              WHEN 'rejected' THEN 2
                              ELSE 3
                            END,
                            bo.created_at DESC,
                            bo.id DESC
                          LIMIT 1
                       ) AS owner_status,
                       (
                         SELECT s.username
                           FROM business_owners bo
                           LEFT JOIN subscribers s ON s.chat_id = bo.tg_user_id
                          WHERE bo.place_id = e.place_id
                          ORDER BY
                            CASE bo.status
                              WHEN 'approved' THEN 0
                              WHEN 'pending' THEN 1
                              WHEN 'rejected' THEN 2
                              ELSE 3
                            END,
                            bo.created_at DESC,
                            bo.id DESC
                          LIMIT 1
                       ) AS owner_username,
                       (
                         SELECT s.first_name
                           FROM business_owners bo
                           LEFT JOIN subscribers s ON s.chat_id = bo.tg_user_id
                          WHERE bo.place_id = e.place_id
                          ORDER BY
                            CASE bo.status
                              WHEN 'approved' THEN 0
                              WHEN 'pending' THEN 1
                              WHEN 'rejected' THEN 2
                              ELSE 3
                            END,
                            bo.created_at DESC,
                            bo.id DESC
                          LIMIT 1
                       ) AS owner_first_name
                  FROM business_payment_events e
                  LEFT JOIN places p ON p.id = e.place_id
                 ORDER BY e.created_at DESC, e.id DESC
                 LIMIT ? OFFSET ?
                """,
                (safe_limit, safe_offset),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def count_all_business_subscriptions(self) -> int:
        async with open_business_db() as db:
            async with db.execute("SELECT COUNT(*) FROM business_subscriptions") as cur:
                row = await cur.fetchone()
                return int(row[0] if row else 0)

    async def list_all_business_subscriptions(
        self,
        *,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 50))
        safe_offset = max(0, int(offset))
        async with open_business_db() as db:
            async with db.execute(
                """
                SELECT bs.place_id, bs.tier, bs.status, bs.starts_at, bs.expires_at,
                       bs.created_at, bs.updated_at,
                       p.name AS place_name, p.address AS place_address,
                       p.business_enabled, p.is_published, p.is_verified, p.verified_tier, p.verified_until,
                       (
                         SELECT bo.tg_user_id
                           FROM business_owners bo
                          WHERE bo.place_id = bs.place_id
                          ORDER BY
                            CASE bo.status
                              WHEN 'approved' THEN 0
                              WHEN 'pending' THEN 1
                              WHEN 'rejected' THEN 2
                              ELSE 3
                            END,
                            bo.created_at DESC,
                            bo.id DESC
                          LIMIT 1
                       ) AS owner_tg_user_id,
                       (
                         SELECT bo.status
                           FROM business_owners bo
                          WHERE bo.place_id = bs.place_id
                          ORDER BY
                            CASE bo.status
                              WHEN 'approved' THEN 0
                              WHEN 'pending' THEN 1
                              WHEN 'rejected' THEN 2
                              ELSE 3
                            END,
                            bo.created_at DESC,
                            bo.id DESC
                          LIMIT 1
                       ) AS owner_status,
                       (
                         SELECT bo.created_at
                           FROM business_owners bo
                          WHERE bo.place_id = bs.place_id
                          ORDER BY
                            CASE bo.status
                              WHEN 'approved' THEN 0
                              WHEN 'pending' THEN 1
                              WHEN 'rejected' THEN 2
                              ELSE 3
                            END,
                            bo.created_at DESC,
                            bo.id DESC
                          LIMIT 1
                       ) AS owner_created_at,
                       (
                         SELECT s.username
                           FROM business_owners bo
                           LEFT JOIN subscribers s ON s.chat_id = bo.tg_user_id
                          WHERE bo.place_id = bs.place_id
                          ORDER BY
                            CASE bo.status
                              WHEN 'approved' THEN 0
                              WHEN 'pending' THEN 1
                              WHEN 'rejected' THEN 2
                              ELSE 3
                            END,
                            bo.created_at DESC,
                            bo.id DESC
                          LIMIT 1
                       ) AS owner_username,
                       (
                         SELECT s.first_name
                           FROM business_owners bo
                           LEFT JOIN subscribers s ON s.chat_id = bo.tg_user_id
                          WHERE bo.place_id = bs.place_id
                          ORDER BY
                            CASE bo.status
                              WHEN 'approved' THEN 0
                              WHEN 'pending' THEN 1
                              WHEN 'rejected' THEN 2
                              ELSE 3
                            END,
                            bo.created_at DESC,
                            bo.id DESC
                          LIMIT 1
                       ) AS owner_first_name
                  FROM business_subscriptions bs
                  JOIN places p ON p.id = bs.place_id
                 ORDER BY
                   CASE bs.status
                     WHEN 'active' THEN 0
                     WHEN 'past_due' THEN 1
                     WHEN 'inactive' THEN 2
                     WHEN 'canceled' THEN 3
                     ELSE 4
                   END,
                   bs.updated_at DESC,
                   bs.place_id ASC
                 LIMIT ? OFFSET ?
                """,
                (safe_limit, safe_offset),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def count_business_audit_logs(self, *, place_id: int | None = None) -> int:
        query = "SELECT COUNT(*) FROM business_audit_log"
        params: tuple[Any, ...] = ()
        if place_id is not None:
            query += " WHERE place_id = ?"
            params = (int(place_id),)
        async with open_business_db() as db:
            async with db.execute(query, params) as cur:
                row = await cur.fetchone()
                return int(row[0] if row else 0)

    async def list_business_audit_logs(
        self,
        *,
        limit: int,
        offset: int,
        place_id: int | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, int(offset))
        where_clause = ""
        params: list[Any] = []
        if place_id is not None:
            where_clause = "WHERE a.place_id = ?"
            params.append(int(place_id))
        params.extend([safe_limit, safe_offset])
        async with open_business_db() as db:
            async with db.execute(
                f"""
                SELECT a.id, a.place_id, a.actor_tg_user_id, a.action, a.payload_json, a.created_at,
                       p.name AS place_name
                  FROM business_audit_log a
                  LEFT JOIN places p ON p.id = a.place_id
                  {where_clause}
                 ORDER BY a.created_at DESC, a.id DESC
                 LIMIT ? OFFSET ?
                """,
                tuple(params),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]

    async def create_claim_token(
        self,
        place_id: int,
        token: str,
        created_by: int,
        expires_at: str,
        attempts_left: int = 5,
    ) -> None:
        created_at = utc_now_iso()
        async with open_business_db() as db:
            await execute_write_with_retry(
                db,
                """INSERT INTO business_claim_tokens(
                       place_id, token, status, attempts_left,
                       created_at, expires_at, created_by,
                       used_at, used_by
                   ) VALUES(?, ?, 'active', ?, ?, ?, ?, NULL, NULL)""",
                (place_id, token, attempts_left, created_at, expires_at, created_by),
            )

    async def get_claim_token(self, token: str) -> dict[str, Any] | None:
        async with open_business_db() as db:
            async with db.execute(
                """SELECT id, place_id, token, status, attempts_left,
                          created_at, expires_at, created_by, used_at, used_by
                     FROM business_claim_tokens
                    WHERE token = ?""",
                (token,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def mark_claim_token_status(
        self,
        token_id: int,
        status: str,
        *,
        used_by: int | None = None,
    ) -> None:
        now = utc_now_iso()
        async with open_business_db() as db:
            await execute_write_with_retry(
                db,
                """UPDATE business_claim_tokens
                      SET status = ?,
                          used_at = ?,
                          used_by = ?
                    WHERE id = ?""",
                (status, now, used_by, token_id),
            )

    async def list_recent_claim_tokens(self, place_id: int, limit: int = 5) -> list[dict[str, Any]]:
        async with open_business_db() as db:
            async with db.execute(
                """SELECT id, place_id, token, status, attempts_left,
                          created_at, expires_at, created_by, used_at, used_by
                     FROM business_claim_tokens
                    WHERE place_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?""",
                (place_id, limit),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(row) for row in rows]
