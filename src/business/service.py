"""Business service abstraction and implementations."""

from __future__ import annotations

import json
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from business.guards import is_business_feature_enabled
from business.repository import BusinessRepository
from config import CFG


PAID_TIERS = {"light", "pro", "partner"}
SUPPORTED_TIERS = {"free", "light", "pro", "partner"}
DEFAULT_SUBSCRIPTION_DAYS = 30


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


class BusinessServiceStub:
    """Safe placeholder for future main-bot integration."""

    async def enrich_places_for_main_bot(self, places: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return places


def get_business_service() -> BusinessService:
    """Resolve enabled service by feature flag."""
    if not is_business_feature_enabled():
        return NoopBusinessService()
    return BusinessServiceStub()


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

    async def register_new_business(
        self,
        tg_user_id: int,
        category_name: str,
        place_name: str,
        description: str,
        address: str,
    ) -> dict[str, Any]:
        """Create new place and owner moderation request."""
        category = category_name.strip()
        name = place_name.strip()
        desc = description.strip()
        addr = address.strip()
        if not category:
            raise ValidationError("Вкажи категорію.")
        if not name:
            raise ValidationError("Назва закладу не може бути порожньою.")
        if len(category) > 80 or len(name) > 120:
            raise ValidationError("Занадто довгі значення для категорії або назви.")
        if len(desc) > 1200 or len(addr) > 300:
            raise ValidationError("Опис або адреса занадто довгі.")

        service_id = await self.repository.get_or_create_service_id(category)
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
                    "category_name": category,
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
        alphabet = string.ascii_uppercase + string.digits

        token = ""
        for _ in range(8):
            candidate = "".join(secrets.choice(alphabet) for _ in range(10))
            existing = await self.repository.get_claim_token(candidate)
            if not existing:
                token = candidate
                break
        if not token:
            raise RuntimeError("Не вдалося згенерувати унікальний claim token.")

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

    async def claim_business_by_token(self, tg_user_id: int, token_raw: str) -> dict[str, Any]:
        """Consume token and create pending owner request."""
        token = token_raw.strip().upper()
        if not token:
            raise ValidationError("Вкажи claim token.")

        token_row = await self.repository.get_claim_token(token)
        if not token_row:
            raise ValidationError("Claim token не знайдено.")
        if token_row["status"] != "active":
            raise ValidationError("Claim token вже неактивний.")

        expires_at_raw = token_row["expires_at"]
        if expires_at_raw:
            expires_at = datetime.fromisoformat(expires_at_raw)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if _utc_now() >= expires_at:
                await self.repository.mark_claim_token_status(token_row["id"], "expired")
                raise ValidationError("Claim token вже прострочений.")

        place = await self.repository.get_place(int(token_row["place_id"]))
        if not place:
            raise NotFoundError("Заклад для claim token не знайдено.")
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
