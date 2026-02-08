"""Business service abstraction and implementations."""

from __future__ import annotations

import logging
import json
import secrets
import sqlite3
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from business.guards import is_business_feature_enabled
from business.repository import BusinessRepository
from config import CFG


logger = logging.getLogger(__name__)

PAID_TIERS = {"light", "pro", "partner"}
SUPPORTED_TIERS = {"free", "light", "pro", "partner"}
DEFAULT_SUBSCRIPTION_DAYS = 30

ADMIN_CLAIM_TOKEN_TTL_DAYS = 365
CLAIM_TOKEN_ALPHABET = string.ascii_uppercase + string.digits
CLAIM_TOKEN_LENGTH = 10
CLAIM_TOKEN_GENERATION_ATTEMPTS = 12
CLAIM_TOKEN_BULK_CHUNK_SIZE = 400  # Keep well under SQLite variable limit.


class BusinessCabinetError(RuntimeError):
    """Base business domain error."""


class AccessDeniedError(BusinessCabinetError):
    """Raised when user cannot access operation."""


class ValidationError(BusinessCabinetError):
    """Raised when input/state is invalid."""


class NotFoundError(BusinessCabinetError):
    """Raised when requested object doesn't exist."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class BusinessService(Protocol):
    """Service contract used by main bot adapters."""

    async def enrich_places_for_main_bot(self, places: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return place list enriched with business metadata."""


class NoopBusinessService:
    """Fallback service when business mode is disabled."""

    async def enrich_places_for_main_bot(self, places: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return places


class BusinessIntegrationService:
    """Read-only business metadata integration for main bot/webapp."""

    def __init__(self, repository: BusinessRepository | None = None) -> None:
        self.repository = repository or BusinessRepository()

    async def enrich_places_for_main_bot(self, places: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not places:
            return places

        place_ids: list[int] = []
        for place in places:
            raw_id = place.get("id")
            try:
                place_ids.append(int(raw_id))
            except Exception:
                continue

        if not place_ids:
            return places

        try:
            meta_map = await self.repository.get_places_business_meta(place_ids)
        except Exception:
            logger.exception("Failed to load business metadata for places")
            return places

        enriched: list[dict[str, Any]] = []
        for place in places:
            raw_id = place.get("id")
            try:
                place_id = int(raw_id)
            except Exception:
                enriched.append(place)
                continue

            meta = meta_map.get(place_id)
            if not meta:
                enriched.append(place)
                continue

            merged = dict(place)
            merged["business_enabled"] = int(meta.get("business_enabled") or 0)
            merged["is_verified"] = int(meta.get("is_verified") or 0)
            merged["verified_tier"] = meta.get("verified_tier")
            merged["verified_until"] = meta.get("verified_until")
            enriched.append(merged)

        return enriched


def get_business_service() -> BusinessService:
    """Resolve enabled service by feature flag."""
    if not is_business_feature_enabled():
        return NoopBusinessService()
    return BusinessIntegrationService()


class BusinessCabinetService:
    """Use-cases for standalone business bot runtime."""

    def __init__(self, repository: BusinessRepository | None = None) -> None:
        self.repository = repository or BusinessRepository()
        self.admin_ids = set(CFG.admin_ids)

    def is_admin(self, tg_user_id: int) -> bool:
        return tg_user_id in self.admin_ids

    def _require_admin(self, tg_user_id: int) -> None:
        if not self.is_admin(tg_user_id):
            raise AccessDeniedError("Ця дія доступна лише адміністратору.")

    async def _generate_unique_claim_token(self) -> str:
        token = ""
        for _ in range(CLAIM_TOKEN_GENERATION_ATTEMPTS):
            candidate = "".join(secrets.choice(CLAIM_TOKEN_ALPHABET) for _ in range(CLAIM_TOKEN_LENGTH))
            existing = await self.repository.get_claim_token(candidate)
            if not existing:
                token = candidate
                break
        if not token:
            raise RuntimeError("Не вдалося згенерувати унікальний код прив'язки.")
        return token

    def _default_claim_token_expires_at(self) -> str:
        return (_utc_now() + timedelta(days=ADMIN_CLAIM_TOKEN_TTL_DAYS)).isoformat()

    async def register_new_business(
        self,
        tg_user_id: int,
        service_id: int,
        place_name: str,
        description: str,
        address: str,
    ) -> dict[str, Any]:
        """Create new place and owner moderation request."""
        if not isinstance(service_id, int) or service_id <= 0:
            raise ValidationError("Оберіть категорію зі списку.")
        service = await self.repository.get_service(service_id)
        if not service:
            raise ValidationError("Категорію не знайдено. Обери зі списку ще раз.")
        name = place_name.strip()
        desc = description.strip()
        addr = address.strip()
        if not name:
            raise ValidationError("Назва закладу не може бути порожньою.")
        if len(name) > 120:
            raise ValidationError("Назва занадто довга.")
        if len(desc) > 1200 or len(addr) > 300:
            raise ValidationError("Опис або адреса занадто довгі.")

        place_id = await self.repository.create_place(
            service_id=service_id,
            name=name,
            description=desc,
            address=addr,
        )
        owner = await self.repository.upsert_owner_request(place_id=place_id, tg_user_id=tg_user_id)
        await self.repository.ensure_subscription(place_id=place_id)
        await self.repository.write_audit_log(
            place_id=place_id,
            actor_tg_user_id=tg_user_id,
            action="owner_request_created",
            payload_json=_to_json(
                {
                    "owner_id": owner["id"],
                    "source": "new_business",
                    "service_id": service_id,
                    "service_name": service["name"],
                }
            ),
        )

        place = await self.repository.get_place(place_id)
        return {
            "owner": owner,
            "place": place,
        }

    async def create_claim_token(
        self,
        admin_tg_user_id: int,
        place_id: int,
        ttl_hours: int = 72,
    ) -> dict[str, Any]:
        """Admin-only token generation for place claim flow."""
        self._require_admin(admin_tg_user_id)
        if ttl_hours < 1 or ttl_hours > 24 * 30:
            raise ValidationError("TTL має бути в межах 1..720 годин.")

        place = await self.repository.get_place(place_id)
        if not place:
            raise NotFoundError("Заклад не знайдено.")

        expires_at_dt = _utc_now() + timedelta(hours=ttl_hours)
        expires_at = expires_at_dt.isoformat()
        token = await self._generate_unique_claim_token()

        await self.repository.create_claim_token(
            place_id=place_id,
            token=token,
            created_by=admin_tg_user_id,
            expires_at=expires_at,
        )
        await self.repository.write_audit_log(
            place_id=place_id,
            actor_tg_user_id=admin_tg_user_id,
            action="claim_token_created",
            payload_json=_to_json(
                {
                    "token": token,
                    "expires_at": expires_at,
                }
            ),
        )
        return {
            "token": token,
            "expires_at": expires_at,
            "place": place,
        }

    async def get_or_create_active_claim_token_for_place(
        self,
        admin_tg_user_id: int,
        place_id: int,
    ) -> dict[str, Any]:
        """Admin-only: return active claim token for place, create if missing/expired/used."""
        self._require_admin(admin_tg_user_id)
        place = await self.repository.get_place(place_id)
        if not place:
            raise NotFoundError("Заклад не знайдено.")

        now_iso = _utc_now().isoformat()
        existing = await self.repository.get_active_claim_token_for_place(place_id, now_iso=now_iso)
        if existing:
            return {"place": place, "token_row": existing}

        token = await self._generate_unique_claim_token()
        expires_at = self._default_claim_token_expires_at()
        await self.repository.rotate_claim_tokens_for_places(
            [place_id],
            [token],
            created_by=admin_tg_user_id,
            expires_at=expires_at,
        )
        await self.repository.write_audit_log(
            place_id=place_id,
            actor_tg_user_id=admin_tg_user_id,
            action="claim_token_created_admin_ui",
            payload_json=_to_json({"token": token, "expires_at": expires_at}),
        )
        created = await self.repository.get_active_claim_token_for_place(place_id, now_iso=now_iso)
        return {"place": place, "token_row": created or {"token": token, "expires_at": expires_at}}

    async def rotate_claim_token_for_place(
        self,
        admin_tg_user_id: int,
        place_id: int,
    ) -> dict[str, Any]:
        """Admin-only: revoke existing active token(s) and create a new one."""
        self._require_admin(admin_tg_user_id)
        place = await self.repository.get_place(place_id)
        if not place:
            raise NotFoundError("Заклад не знайдено.")

        token = await self._generate_unique_claim_token()
        expires_at = self._default_claim_token_expires_at()
        await self.repository.rotate_claim_tokens_for_places(
            [place_id],
            [token],
            created_by=admin_tg_user_id,
            expires_at=expires_at,
        )
        await self.repository.write_audit_log(
            place_id=place_id,
            actor_tg_user_id=admin_tg_user_id,
            action="claim_token_rotated_admin_ui",
            payload_json=_to_json({"token": token, "expires_at": expires_at}),
        )
        return {"place": place, "token": token, "expires_at": expires_at}

    async def bulk_rotate_claim_tokens_for_all_places(self, admin_tg_user_id: int) -> dict[str, Any]:
        """Admin-only: rotate claim tokens for every place."""
        self._require_admin(admin_tg_user_id)
        place_ids = await self.repository.list_all_place_ids()
        if not place_ids:
            return {"total_places": 0, "rotated": 0}

        expires_at = self._default_claim_token_expires_at()
        rotated = 0

        # Generate unique tokens within the batch. We don't pre-check DB uniqueness here:
        # collisions are extremely unlikely; if it happens, we retry the chunk.
        for start in range(0, len(place_ids), CLAIM_TOKEN_BULK_CHUNK_SIZE):
            chunk_ids = place_ids[start : start + CLAIM_TOKEN_BULK_CHUNK_SIZE]
            for attempt in range(3):
                used: set[str] = set()
                chunk_tokens: list[str] = []
                for _pid in chunk_ids:
                    while True:
                        candidate = "".join(secrets.choice(CLAIM_TOKEN_ALPHABET) for _ in range(CLAIM_TOKEN_LENGTH))
                        if candidate in used:
                            continue
                        used.add(candidate)
                        chunk_tokens.append(candidate)
                        break
                try:
                    await self.repository.rotate_claim_tokens_for_places(
                        chunk_ids,
                        chunk_tokens,
                        created_by=admin_tg_user_id,
                        expires_at=expires_at,
                    )
                    rotated += len(chunk_ids)
                    break
                except sqlite3.IntegrityError:
                    if attempt >= 2:
                        raise
                    continue
        return {"total_places": len(place_ids), "rotated": rotated}

    async def claim_business_by_token(self, tg_user_id: int, token_raw: str) -> dict[str, Any]:
        """Consume token and create pending owner request."""
        token = token_raw.strip().upper().replace("-", "").replace(" ", "")
        if not token:
            raise ValidationError("Вкажи код прив'язки.")

        token_row = await self.repository.get_claim_token(token)
        if not token_row:
            raise ValidationError("Код прив'язки не знайдено.")
        if token_row["status"] != "active":
            raise ValidationError("Код прив'язки вже неактивний.")

        expires_at_raw = token_row["expires_at"]
        if expires_at_raw:
            expires_at = datetime.fromisoformat(expires_at_raw)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if _utc_now() >= expires_at:
                await self.repository.mark_claim_token_status(token_row["id"], "expired")
                raise ValidationError("Код прив'язки вже прострочений.")

        place = await self.repository.get_place(int(token_row["place_id"]))
        if not place:
            raise NotFoundError("Заклад для цього коду не знайдено.")
        if await self.repository.is_approved_owner(tg_user_id, int(token_row["place_id"])):
            raise ValidationError("Ти вже маєш підтверджений доступ до цього бізнесу.")

        await self.repository.mark_claim_token_status(
            token_row["id"],
            "used",
            used_by=tg_user_id,
        )
        owner = await self.repository.upsert_owner_request(
            place_id=int(token_row["place_id"]),
            tg_user_id=tg_user_id,
        )
        await self.repository.ensure_subscription(place_id=int(token_row["place_id"]))
        await self.repository.write_audit_log(
            place_id=int(token_row["place_id"]),
            actor_tg_user_id=tg_user_id,
            action="owner_claim_requested",
            payload_json=_to_json(
                {
                    "owner_id": owner["id"],
                    "token_id": token_row["id"],
                }
            ),
        )
        return {
            "owner": owner,
            "place": place,
            "token": token,
        }

    async def list_user_businesses(self, tg_user_id: int) -> list[dict[str, Any]]:
        return await self.repository.list_user_businesses(tg_user_id)

    async def list_pending_owner_requests(self, admin_tg_user_id: int) -> list[dict[str, Any]]:
        self._require_admin(admin_tg_user_id)
        return await self.repository.list_pending_owner_requests()

    async def approve_owner_request(self, admin_tg_user_id: int, owner_id: int) -> dict[str, Any]:
        self._require_admin(admin_tg_user_id)
        owner = await self.repository.get_owner_request(owner_id)
        if not owner:
            raise NotFoundError("Заявку не знайдено.")
        if owner["status"] != "pending":
            raise ValidationError("Заявка вже оброблена.")

        updated = await self.repository.update_owner_status(owner_id, "approved", admin_tg_user_id)
        if not updated:
            raise RuntimeError("Не вдалося оновити статус заявки.")

        subscription = await self.repository.ensure_subscription(updated["place_id"])
        tier = subscription["tier"]
        sub_status = subscription["status"]
        verified_until = subscription["expires_at"] if (tier in PAID_TIERS and sub_status == "active") else None
        await self.repository.update_place_business_flags(
            updated["place_id"],
            business_enabled=1,
            is_verified=1 if verified_until else 0,
            verified_tier=tier if verified_until else None,
            verified_until=verified_until,
        )
        await self.repository.write_audit_log(
            place_id=updated["place_id"],
            actor_tg_user_id=admin_tg_user_id,
            action="owner_request_approved",
            payload_json=_to_json({"owner_id": owner_id, "owner_tg_user_id": updated["tg_user_id"]}),
        )
        return updated

    async def reject_owner_request(self, admin_tg_user_id: int, owner_id: int) -> dict[str, Any]:
        self._require_admin(admin_tg_user_id)
        owner = await self.repository.get_owner_request(owner_id)
        if not owner:
            raise NotFoundError("Заявку не знайдено.")
        if owner["status"] != "pending":
            raise ValidationError("Заявка вже оброблена.")

        updated = await self.repository.update_owner_status(owner_id, "rejected", admin_tg_user_id)
        if not updated:
            raise RuntimeError("Не вдалося оновити статус заявки.")

        has_approved = await self.repository.has_approved_owners(updated["place_id"])
        if not has_approved:
            await self.repository.update_place_business_flags(
                updated["place_id"],
                business_enabled=0,
                is_verified=0,
                verified_tier=None,
                verified_until=None,
            )
        await self.repository.write_audit_log(
            place_id=updated["place_id"],
            actor_tg_user_id=admin_tg_user_id,
            action="owner_request_rejected",
            payload_json=_to_json({"owner_id": owner_id, "owner_tg_user_id": updated["tg_user_id"]}),
        )
        return updated

    async def update_place_field(
        self,
        tg_user_id: int,
        place_id: int,
        field: str,
        value: str,
    ) -> dict[str, Any]:
        """Owner-only update for safe editable fields."""
        allowed_fields = {"name", "description", "address"}
        if field not in allowed_fields:
            raise ValidationError("Поле недоступне для редагування.")
        clean_value = value.strip()
        if not clean_value:
            raise ValidationError("Нове значення не може бути порожнім.")
        if len(clean_value) > 1200:
            raise ValidationError("Значення занадто довге.")

        can_edit = await self.repository.is_approved_owner(tg_user_id, place_id)
        if not can_edit:
            raise AccessDeniedError("Ти можеш редагувати лише свої підтверджені заклади.")

        updated_place = await self.repository.update_place_profile_field(
            place_id=place_id,
            field=field,
            value=clean_value,
        )
        if not updated_place:
            raise NotFoundError("Заклад не знайдено.")
        await self.repository.write_audit_log(
            place_id=place_id,
            actor_tg_user_id=tg_user_id,
            action="place_profile_updated",
            payload_json=_to_json({"field": field, "value": clean_value}),
        )
        return updated_place

    async def change_subscription_tier(
        self,
        tg_user_id: int,
        place_id: int,
        tier: str,
    ) -> dict[str, Any]:
        """Owner-level plan switch (manual MVP, no auto-billing)."""
        normalized_tier = tier.strip().lower()
        if normalized_tier not in SUPPORTED_TIERS:
            raise ValidationError("Невідомий тариф.")
        can_manage = await self.repository.is_approved_owner(tg_user_id, place_id)
        if not can_manage:
            raise AccessDeniedError("Ти можеш змінювати тариф лише своїх підтверджених закладів.")

        now = _utc_now()
        if normalized_tier == "free":
            sub_status = "inactive"
            starts_at = None
            expires_at = None
            is_verified = 0
            verified_tier = None
            verified_until = None
        else:
            sub_status = "active"
            starts_at = now.isoformat()
            expires_at = (now + timedelta(days=DEFAULT_SUBSCRIPTION_DAYS)).isoformat()
            is_verified = 1
            verified_tier = normalized_tier
            verified_until = expires_at

        subscription = await self.repository.update_subscription(
            place_id=place_id,
            tier=normalized_tier,
            status=sub_status,
            starts_at=starts_at,
            expires_at=expires_at,
        )
        await self.repository.update_place_business_flags(
            place_id,
            business_enabled=1,
            is_verified=is_verified,
            verified_tier=verified_tier,
            verified_until=verified_until,
        )
        await self.repository.write_audit_log(
            place_id=place_id,
            actor_tg_user_id=tg_user_id,
            action="subscription_tier_changed",
            payload_json=_to_json(
                {
                    "tier": normalized_tier,
                    "status": sub_status,
                    "starts_at": starts_at,
                    "expires_at": expires_at,
                }
            ),
        )
        return subscription
