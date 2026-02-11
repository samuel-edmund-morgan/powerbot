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
from business.payments import (
    MockPaymentProvider,
    TelegramStarsPaymentProvider,
    decode_telegram_stars_payload,
)
from business.repository import BusinessRepository
from config import CFG


logger = logging.getLogger(__name__)

PAID_TIERS = {"light", "pro", "partner"}
SUPPORTED_TIERS = {"free", "light", "pro", "partner"}
DEFAULT_SUBSCRIPTION_DAYS = 30
PLAN_STARS_PRICES: dict[str, int] = {
    "light": 1000,
    "pro": 2500,
    "partner": 5000,
}

PAYMENT_PROVIDER_MOCK = "mock"
PAYMENT_PROVIDER_TELEGRAM_STARS = "telegram_stars"
SUPPORTED_PAYMENT_PROVIDERS = {PAYMENT_PROVIDER_MOCK, PAYMENT_PROVIDER_TELEGRAM_STARS}

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


def _iso_from_unix_utc(timestamp: int | None) -> str | None:
    try:
        value = int(timestamp) if timestamp is not None else 0
    except Exception:
        return None
    if value <= 0:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


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
        self._payment_providers = {
            PAYMENT_PROVIDER_MOCK: MockPaymentProvider(),
            PAYMENT_PROVIDER_TELEGRAM_STARS: TelegramStarsPaymentProvider(),
        }

    def is_admin(self, tg_user_id: int) -> bool:
        return tg_user_id in self.admin_ids

    def _require_admin(self, tg_user_id: int) -> None:
        if not self.is_admin(tg_user_id):
            raise AccessDeniedError("Ця дія доступна лише адміністратору.")

    def get_payment_provider(self) -> str:
        raw = (CFG.business_payment_provider or "").strip().lower()
        if raw in SUPPORTED_PAYMENT_PROVIDERS:
            return raw
        return PAYMENT_PROVIDER_TELEGRAM_STARS

    def get_plan_price_stars(self, tier: str) -> int:
        return int(PLAN_STARS_PRICES.get(str(tier).strip().lower(), 0))

    def _get_payment_provider_impl(self, provider_name: str):
        provider = self._payment_providers.get(str(provider_name).strip().lower())
        if not provider:
            raise ValidationError("Невідомий провайдер оплати в конфігурації.")
        return provider

    async def _assert_paid_plan_access(
        self,
        *,
        tg_user_id: int,
        place_id: int,
        tier: str,
    ) -> tuple[str, int]:
        normalized_tier = str(tier).strip().lower()
        if normalized_tier not in PAID_TIERS:
            raise ValidationError("Для цього тарифу оплата не потрібна.")
        can_manage = await self.repository.is_approved_owner(tg_user_id, place_id)
        if not can_manage:
            raise AccessDeniedError("Ти можеш змінювати тариф лише своїх підтверджених закладів.")
        amount_stars = self.get_plan_price_stars(normalized_tier)
        if amount_stars <= 0:
            raise ValidationError("Для цього тарифу ще не налаштована ціна.")
        return normalized_tier, amount_stars

    async def _payment_intent_exists(
        self,
        *,
        provider: str,
        external_payment_id: str,
        place_id: int,
    ) -> bool:
        if not external_payment_id:
            return False
        existing = await self.repository.get_payment_events_by_external_id(
            provider=str(provider),
            external_payment_id=str(external_payment_id),
        )
        return any(
            int(row.get("place_id") or 0) == int(place_id) and str(row.get("event_type") or "") == "invoice_created"
            for row in existing
        )

    async def apply_payment_event(
        self,
        *,
        tg_user_id: int,
        place_id: int,
        tier: str,
        provider: str,
        intent_external_payment_id: str,
        payment_external_id: str | None,
        event_type: str,
        amount_stars: int,
        source: str = "card",
        currency: str = "XTR",
        status: str = "processed",
        expires_at: str | None = None,
        raw_payload_json: str | None = None,
        audit_extra: dict[str, Any] | None = None,
        write_non_success_audit: bool = True,
    ) -> dict[str, Any]:
        """Apply canonical payment event and update business state idempotently."""
        normalized_event = str(event_type or "").strip().lower()
        if normalized_event not in {
            "invoice_created",
            "pre_checkout_ok",
            "payment_succeeded",
            "payment_failed",
            "payment_canceled",
            "refund",
        }:
            raise ValidationError("Непідтримуваний тип платіжної події.")

        payment_event_external_id = str(payment_external_id or "").strip() or str(intent_external_payment_id or "").strip()
        if not payment_event_external_id:
            raise ValidationError("Некоректний ідентифікатор платежу.")

        inserted = await self.repository.create_payment_event(
            place_id=int(place_id),
            provider=str(provider),
            external_payment_id=payment_event_external_id,
            event_type=normalized_event,
            amount_stars=int(amount_stars),
            currency=str(currency or "XTR"),
            status=str(status or "processed"),
            raw_payload_json=raw_payload_json,
            processed_at=_utc_now().isoformat(),
        )
        if not inserted:
            return {
                "applied": False,
                "duplicate": True,
                "event_type": normalized_event,
                "place_id": int(place_id),
                "tier": str(tier).strip().lower(),
                "source": str(source or "card"),
                "payment_external_id": payment_event_external_id,
                "external_payment_id": str(intent_external_payment_id or ""),
                "subscription": None,
            }

        subscription: dict[str, Any] | None = None
        if normalized_event == "payment_succeeded":
            success_audit_extra: dict[str, Any] = {
                "provider": str(provider),
                "intent_external_payment_id": str(intent_external_payment_id or ""),
                "payment_external_id": payment_event_external_id,
                "amount_stars": int(amount_stars),
                "source": str(source or "card"),
            }
            if audit_extra:
                success_audit_extra.update(audit_extra)
            subscription = await self._activate_paid_subscription(
                tg_user_id=int(tg_user_id),
                place_id=int(place_id),
                tier=str(tier).strip().lower(),
                expires_at=expires_at,
                audit_extra=success_audit_extra,
            )
        elif write_non_success_audit and normalized_event in {"payment_failed", "payment_canceled", "refund"}:
            non_success_payload: dict[str, Any] = {
                "provider": str(provider),
                "intent_external_payment_id": str(intent_external_payment_id or ""),
                "payment_external_id": payment_event_external_id,
                "tier": str(tier).strip().lower(),
                "amount_stars": int(amount_stars),
                "source": str(source or "card"),
            }
            if audit_extra:
                non_success_payload.update(audit_extra)
            await self.repository.write_audit_log(
                place_id=int(place_id),
                actor_tg_user_id=int(tg_user_id),
                action=normalized_event,
                payload_json=_to_json(non_success_payload),
            )

        return {
            "applied": True,
            "duplicate": False,
            "event_type": normalized_event,
            "place_id": int(place_id),
            "tier": str(tier).strip().lower(),
            "source": str(source or "card"),
            "payment_external_id": payment_event_external_id,
            "external_payment_id": str(intent_external_payment_id or ""),
            "subscription": subscription,
        }

    async def create_payment_intent(
        self,
        *,
        tg_user_id: int,
        place_id: int,
        tier: str,
        source: str = "card",
    ) -> dict[str, Any]:
        normalized_tier, amount_stars = await self._assert_paid_plan_access(
            tg_user_id=tg_user_id,
            place_id=place_id,
            tier=tier,
        )
        provider_name = self.get_payment_provider()
        provider = self._get_payment_provider_impl(provider_name)
        intent = provider.create_intent(
            tg_user_id=int(tg_user_id),
            place_id=int(place_id),
            tier=normalized_tier,
            amount_stars=amount_stars,
            source=str(source or "card"),
        )
        raw_payload: dict[str, Any] = {
            "source": f"businessbot_{provider_name}_ui",
            "tg_user_id": int(tg_user_id),
            "place_id": int(place_id),
            "tier": normalized_tier,
            "amount_stars": int(amount_stars),
            "source_menu": str(source or "card"),
        }
        if intent.invoice_payload:
            raw_payload["invoice_payload"] = str(intent.invoice_payload)
        inserted = await self.repository.create_payment_event(
            place_id=int(place_id),
            provider=str(intent.provider),
            external_payment_id=str(intent.external_payment_id),
            event_type="invoice_created",
            amount_stars=int(amount_stars),
            currency="XTR",
            status="new",
            raw_payload_json=_to_json(raw_payload),
            processed_at=None,
        )
        if not inserted:
            raise RuntimeError("Не вдалося підготувати оплату. Спробуй ще раз.")
        return {
            "provider": str(intent.provider),
            "external_payment_id": str(intent.external_payment_id),
            "amount_stars": int(amount_stars),
            "tier": normalized_tier,
            "source": str(source or "card"),
            "invoice_payload": str(intent.invoice_payload) if intent.invoice_payload else None,
        }

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

    async def list_all_subscriptions_admin(
        self,
        admin_tg_user_id: int,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        self._require_admin(admin_tg_user_id)
        rows = await self.repository.list_all_business_subscriptions(limit=limit, offset=offset)
        total = await self.repository.count_all_business_subscriptions()
        return rows, total

    async def list_audit_logs_admin(
        self,
        admin_tg_user_id: int,
        *,
        limit: int,
        offset: int,
        place_id: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        self._require_admin(admin_tg_user_id)
        rows = await self.repository.list_business_audit_logs(limit=limit, offset=offset, place_id=place_id)
        total = await self.repository.count_business_audit_logs(place_id=place_id)
        return rows, total

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
        # New places created via business bot are created unpublished to avoid spam in resident catalog.
        # Publishing happens only after admin approval of the ownership request.
        await self.repository.set_place_published(updated["place_id"], is_published=1)
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

    async def get_pending_owner_request_for_place(
        self,
        admin_tg_user_id: int,
        place_id: int,
    ) -> dict[str, Any] | None:
        self._require_admin(admin_tg_user_id)
        return await self.repository.get_pending_owner_request_for_place(int(place_id))

    async def set_place_published(
        self,
        admin_tg_user_id: int,
        place_id: int,
        *,
        is_published: int,
    ) -> dict[str, Any]:
        """Admin-only: toggle place publication in resident catalog."""
        self._require_admin(admin_tg_user_id)
        place = await self.repository.get_place(int(place_id))
        if not place:
            raise NotFoundError("Заклад не знайдено.")
        await self.repository.set_place_published(int(place_id), is_published=is_published)
        await self.repository.write_audit_log(
            place_id=int(place_id),
            actor_tg_user_id=admin_tg_user_id,
            action="place_publish_toggled",
            payload_json=_to_json({"is_published": 1 if int(is_published) else 0}),
        )
        updated = await self.repository.get_place(int(place_id))
        return updated or place

    async def delete_place_draft(self, admin_tg_user_id: int, place_id: int) -> dict[str, Any]:
        """Admin-only: delete an unpublished draft place (anti-spam)."""
        self._require_admin(admin_tg_user_id)
        place = await self.repository.get_place(int(place_id))
        if not place:
            raise NotFoundError("Заклад не знайдено.")
        if int(place.get("is_published") or 0) != 0:
            raise ValidationError("Можна видаляти лише неопубліковані чернетки.")

        ok = await self.repository.delete_place_draft(int(place_id))
        if not ok:
            raise RuntimeError("Не вдалося видалити заклад.")

        # Keep audit trail even if the place row is removed (no FK constraints).
        await self.repository.write_audit_log(
            place_id=int(place_id),
            actor_tg_user_id=admin_tg_user_id,
            action="place_draft_deleted",
            payload_json=_to_json(
                {
                    "snapshot": {
                        "service_id": place.get("service_id"),
                        "service_name": place.get("service_name"),
                        "name": place.get("name"),
                        "address": place.get("address"),
                    }
                }
            ),
        )
        return place

    async def admin_create_service(self, admin_tg_user_id: int, service_name: str) -> dict[str, Any]:
        self._require_admin(admin_tg_user_id)
        clean_name = str(service_name or "").strip()
        if not clean_name:
            raise ValidationError("Назва категорії не може бути порожньою.")
        if len(clean_name) > 80:
            raise ValidationError("Назва категорії занадто довга.")
        try:
            result = await self.repository.create_service_if_missing(clean_name)
        except sqlite3.IntegrityError:
            raise ValidationError("Категорія з такою назвою вже існує.")
        return result

    async def admin_rename_service(
        self,
        admin_tg_user_id: int,
        service_id: int,
        service_name: str,
    ) -> dict[str, Any]:
        self._require_admin(admin_tg_user_id)
        clean_name = str(service_name or "").strip()
        if not clean_name:
            raise ValidationError("Назва категорії не може бути порожньою.")
        if len(clean_name) > 80:
            raise ValidationError("Назва категорії занадто довга.")

        service = await self.repository.get_service(int(service_id))
        if not service:
            raise NotFoundError("Категорію не знайдено.")
        try:
            updated = await self.repository.rename_service(int(service_id), clean_name)
        except sqlite3.IntegrityError:
            raise ValidationError("Категорія з такою назвою вже існує.")
        if not updated:
            raise RuntimeError("Не вдалося оновити назву категорії.")
        return {"id": int(service_id), "name": clean_name}

    async def admin_create_place(
        self,
        admin_tg_user_id: int,
        *,
        service_id: int,
        name: str,
        description: str,
        building_id: int,
        address_details: str = "",
        is_published: int = 1,
    ) -> dict[str, Any]:
        self._require_admin(admin_tg_user_id)
        service = await self.repository.get_service(int(service_id))
        if not service:
            raise ValidationError("Категорію не знайдено.")
        building = await self.repository.get_building(int(building_id))
        if not building:
            raise ValidationError("Будинок не знайдено.")

        clean_name = str(name or "").strip()
        clean_description = str(description or "").strip()
        clean_details = str(address_details or "").strip()
        if not clean_name:
            raise ValidationError("Назва закладу не може бути порожньою.")
        if len(clean_name) > 120:
            raise ValidationError("Назва закладу занадто довга.")
        if len(clean_description) > 1200:
            raise ValidationError("Опис занадто довгий.")
        if len(clean_details) > 300:
            raise ValidationError("Деталі адреси занадто довгі.")

        building_label = f"{building['name']} ({building['address']})"
        address = building_label if not clean_details else f"{building_label}, {clean_details}"

        place_id = await self.repository.create_place(
            service_id=int(service_id),
            name=clean_name,
            description=clean_description,
            address=address,
        )
        await self.repository.ensure_subscription(place_id)
        if int(is_published):
            await self.repository.set_place_published(place_id, is_published=1)
        await self.repository.write_audit_log(
            place_id=place_id,
            actor_tg_user_id=int(admin_tg_user_id),
            action="admin_place_created",
            payload_json=_to_json(
                {
                    "service_id": int(service_id),
                    "service_name": str(service.get("name") or ""),
                    "building_id": int(building_id),
                    "address": address,
                    "is_published": 1 if int(is_published) else 0,
                }
            ),
        )
        place = await self.repository.get_place(place_id)
        if not place:
            raise RuntimeError("Не вдалося прочитати створений заклад.")
        return place

    async def admin_update_place_field(
        self,
        admin_tg_user_id: int,
        *,
        place_id: int,
        field: str,
        value: str,
    ) -> dict[str, Any]:
        self._require_admin(admin_tg_user_id)
        allowed_fields = {"name", "description", "address"}
        normalized_field = str(field or "").strip().lower()
        if normalized_field not in allowed_fields:
            raise ValidationError("Поле недоступне для редагування.")
        clean_value = str(value or "").strip()
        if not clean_value:
            raise ValidationError("Нове значення не може бути порожнім.")
        if len(clean_value) > 1200:
            raise ValidationError("Значення занадто довге.")
        if normalized_field == "name" and len(clean_value) > 120:
            raise ValidationError("Назва закладу занадто довга.")
        if normalized_field == "address" and len(clean_value) > 300:
            raise ValidationError("Адреса занадто довга.")

        updated_place = await self.repository.update_place_profile_field(
            place_id=int(place_id),
            field=normalized_field,
            value=clean_value,
        )
        if not updated_place:
            raise NotFoundError("Заклад не знайдено.")
        await self.repository.write_audit_log(
            place_id=int(place_id),
            actor_tg_user_id=int(admin_tg_user_id),
            action="admin_place_profile_updated",
            payload_json=_to_json({"field": normalized_field, "value": clean_value}),
        )
        return updated_place

    async def admin_set_subscription_tier(
        self,
        admin_tg_user_id: int,
        *,
        place_id: int,
        tier: str,
        months: int = 1,
    ) -> dict[str, Any]:
        self._require_admin(admin_tg_user_id)
        place = await self.repository.get_place(int(place_id))
        if not place:
            raise NotFoundError("Заклад не знайдено.")

        normalized_tier = str(tier or "").strip().lower()
        if normalized_tier not in SUPPORTED_TIERS:
            raise ValidationError("Невідомий тариф.")
        safe_months = max(1, min(int(months), 12))
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
            expires_at = (now + timedelta(days=30 * safe_months)).isoformat()
            is_verified = 1
            verified_tier = normalized_tier
            verified_until = expires_at

        subscription = await self.repository.update_subscription(
            place_id=int(place_id),
            tier=normalized_tier,
            status=sub_status,
            starts_at=starts_at,
            expires_at=expires_at,
        )
        await self.repository.update_place_business_flags(
            int(place_id),
            business_enabled=1,
            is_verified=is_verified,
            verified_tier=verified_tier,
            verified_until=verified_until,
        )
        await self.repository.write_audit_log(
            place_id=int(place_id),
            actor_tg_user_id=int(admin_tg_user_id),
            action="admin_subscription_set",
            payload_json=_to_json(
                {
                    "tier": normalized_tier,
                    "status": sub_status,
                    "months": safe_months,
                    "starts_at": starts_at,
                    "expires_at": expires_at,
                }
            ),
        )
        return subscription

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

    async def _activate_paid_subscription(
        self,
        *,
        tg_user_id: int,
        place_id: int,
        tier: str,
        expires_at: str | None = None,
        audit_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Activate paid tier and sync verified flags."""
        normalized_tier = str(tier).strip().lower()
        if normalized_tier not in PAID_TIERS:
            raise ValidationError("Для цього тарифу потрібен платний план.")
        can_manage = await self.repository.is_approved_owner(tg_user_id, place_id)
        if not can_manage:
            raise AccessDeniedError("Ти можеш змінювати тариф лише своїх підтверджених закладів.")

        now = _utc_now()
        starts_at = now.isoformat()
        effective_expires = str(expires_at or "").strip()
        if not effective_expires:
            effective_expires = (now + timedelta(days=DEFAULT_SUBSCRIPTION_DAYS)).isoformat()
        else:
            try:
                parsed_expires = datetime.fromisoformat(effective_expires)
                if parsed_expires.tzinfo is None:
                    parsed_expires = parsed_expires.replace(tzinfo=timezone.utc)
                if parsed_expires <= now:
                    effective_expires = (now + timedelta(days=DEFAULT_SUBSCRIPTION_DAYS)).isoformat()
            except Exception:
                effective_expires = (now + timedelta(days=DEFAULT_SUBSCRIPTION_DAYS)).isoformat()

        subscription = await self.repository.update_subscription(
            place_id=place_id,
            tier=normalized_tier,
            status="active",
            starts_at=starts_at,
            expires_at=effective_expires,
        )
        await self.repository.update_place_business_flags(
            place_id,
            business_enabled=1,
            is_verified=1,
            verified_tier=normalized_tier,
            verified_until=effective_expires,
        )
        payload: dict[str, Any] = {
            "tier": normalized_tier,
            "status": "active",
            "starts_at": starts_at,
            "expires_at": effective_expires,
        }
        if audit_extra:
            payload.update(audit_extra)
        await self.repository.write_audit_log(
            place_id=place_id,
            actor_tg_user_id=tg_user_id,
            action="subscription_tier_changed",
            payload_json=_to_json(payload),
        )
        return subscription

    async def change_subscription_tier(
        self,
        tg_user_id: int,
        place_id: int,
        tier: str,
    ) -> dict[str, Any]:
        """Apply subscription tier change and sync verified flags."""
        normalized_tier = tier.strip().lower()
        if normalized_tier not in SUPPORTED_TIERS:
            raise ValidationError("Невідомий тариф.")
        can_manage = await self.repository.is_approved_owner(tg_user_id, place_id)
        if not can_manage:
            raise AccessDeniedError("Ти можеш змінювати тариф лише своїх підтверджених закладів.")

        if normalized_tier == "free":
            now = _utc_now()
            sub_status = "inactive"
            starts_at = None
            expires_at = None
            is_verified = 0
            verified_tier = None
            verified_until = None
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

        return await self._activate_paid_subscription(
            tg_user_id=tg_user_id,
            place_id=place_id,
            tier=normalized_tier,
        )

    async def create_mock_payment_intent(
        self,
        *,
        tg_user_id: int,
        place_id: int,
        tier: str,
        source: str = "card",
    ) -> dict[str, Any]:
        """Backward-compatible wrapper for mock payment intent creation."""
        provider = self.get_payment_provider()
        if provider != PAYMENT_PROVIDER_MOCK:
            raise ValidationError("Mock-оплата доступна лише у test-режимі.")
        return await self.create_payment_intent(
            tg_user_id=tg_user_id,
            place_id=place_id,
            tier=tier,
            source=source,
        )

    async def apply_mock_payment_result(
        self,
        *,
        tg_user_id: int,
        place_id: int,
        tier: str,
        external_payment_id: str,
        result: str,
    ) -> dict[str, Any]:
        """Apply mock payment result idempotently."""
        normalized_tier, amount_stars = await self._assert_paid_plan_access(
            tg_user_id=tg_user_id,
            place_id=place_id,
            tier=tier,
        )
        if not external_payment_id:
            raise ValidationError("Сесія оплати не знайдена. Спробуй ще раз.")

        if not await self._payment_intent_exists(
            provider=PAYMENT_PROVIDER_MOCK,
            external_payment_id=external_payment_id,
            place_id=int(place_id),
        ):
            raise ValidationError("Сесія оплати застаріла або не знайдена. Запусти оплату ще раз.")

        normalized_result = str(result).strip().lower()
        if normalized_result not in {"success", "cancel", "fail"}:
            raise ValidationError("Невідомий результат оплати.")

        event_type_map = {
            "success": "payment_succeeded",
            "cancel": "payment_canceled",
            "fail": "payment_failed",
        }
        status_map = {
            "success": "processed",
            "cancel": "failed",
            "fail": "failed",
        }
        event_type = event_type_map[normalized_result]
        outcome = await self.apply_payment_event(
            tg_user_id=int(tg_user_id),
            place_id=int(place_id),
            tier=normalized_tier,
            provider=PAYMENT_PROVIDER_MOCK,
            intent_external_payment_id=str(external_payment_id),
            payment_external_id=str(external_payment_id),
            event_type=event_type,
            amount_stars=int(amount_stars),
            source="card",
            currency="XTR",
            status=status_map[normalized_result],
            raw_payload_json=_to_json(
                {
                    "source": "businessbot_mock_ui",
                    "result": normalized_result,
                    "tg_user_id": int(tg_user_id),
                    "place_id": int(place_id),
                    "tier": normalized_tier,
                }
            ),
            audit_extra={"result": normalized_result},
        )
        outcome["result"] = normalized_result
        return outcome

    async def validate_telegram_stars_pre_checkout(
        self,
        *,
        tg_user_id: int,
        invoice_payload: str,
        total_amount: int,
        currency: str,
        pre_checkout_query_id: str | None = None,
    ) -> dict[str, Any]:
        provider = self.get_payment_provider()
        if provider != PAYMENT_PROVIDER_TELEGRAM_STARS:
            raise ValidationError("Оплати тимчасово недоступні.")

        payload = decode_telegram_stars_payload(invoice_payload)
        if not payload:
            raise ValidationError("Некоректні дані рахунку.")
        if int(payload.tg_user_id) != int(tg_user_id):
            raise ValidationError("Цей рахунок належить іншому користувачу.")

        normalized_tier, expected_amount = await self._assert_paid_plan_access(
            tg_user_id=int(tg_user_id),
            place_id=int(payload.place_id),
            tier=str(payload.tier),
        )
        if str(currency or "").upper() != "XTR":
            raise ValidationError("Підтримується лише оплата у Stars (XTR).")
        if int(total_amount) != int(expected_amount):
            raise ValidationError("Сума рахунку не збігається з тарифом.")

        if not await self._payment_intent_exists(
            provider=PAYMENT_PROVIDER_TELEGRAM_STARS,
            external_payment_id=str(payload.external_payment_id),
            place_id=int(payload.place_id),
        ):
            raise ValidationError("Сесія оплати застаріла. Спробуй ще раз.")

        await self.apply_payment_event(
            tg_user_id=int(tg_user_id),
            place_id=int(payload.place_id),
            tier=normalized_tier,
            provider=PAYMENT_PROVIDER_TELEGRAM_STARS,
            intent_external_payment_id=str(payload.external_payment_id),
            payment_external_id=str(payload.external_payment_id),
            event_type="pre_checkout_ok",
            amount_stars=int(expected_amount),
            source=str(payload.source or "card"),
            currency="XTR",
            status="processed",
            raw_payload_json=_to_json(
                {
                    "source": "telegram_stars_pre_checkout",
                    "tg_user_id": int(tg_user_id),
                    "pre_checkout_query_id": str(pre_checkout_query_id or ""),
                    "invoice_payload": str(invoice_payload),
                }
            ),
            write_non_success_audit=False,
        )
        return {
            "place_id": int(payload.place_id),
            "tier": normalized_tier,
            "amount_stars": int(expected_amount),
            "external_payment_id": str(payload.external_payment_id),
            "source": str(payload.source or "card"),
        }

    async def apply_telegram_stars_successful_payment(
        self,
        *,
        tg_user_id: int,
        invoice_payload: str,
        total_amount: int,
        currency: str,
        subscription_expiration_date: int | None,
        is_recurring: bool | None,
        is_first_recurring: bool | None,
        telegram_payment_charge_id: str | None,
        provider_payment_charge_id: str | None,
        raw_payload_json: str | None,
    ) -> dict[str, Any]:
        provider = self.get_payment_provider()
        if provider != PAYMENT_PROVIDER_TELEGRAM_STARS:
            raise ValidationError("Оплати тимчасово недоступні.")

        payload = decode_telegram_stars_payload(invoice_payload)
        if not payload:
            raise ValidationError("Некоректні дані платежу.")
        if int(payload.tg_user_id) != int(tg_user_id):
            raise ValidationError("Цей платіж належить іншому користувачу.")

        normalized_tier, expected_amount = await self._assert_paid_plan_access(
            tg_user_id=int(tg_user_id),
            place_id=int(payload.place_id),
            tier=str(payload.tier),
        )
        if str(currency or "").upper() != "XTR":
            raise ValidationError("Підтримується лише оплата у Stars (XTR).")
        if int(total_amount) != int(expected_amount):
            raise ValidationError("Сума платежу не збігається з тарифом.")

        if not await self._payment_intent_exists(
            provider=PAYMENT_PROVIDER_TELEGRAM_STARS,
            external_payment_id=str(payload.external_payment_id),
            place_id=int(payload.place_id),
        ):
            raise ValidationError("Сесія оплати застаріла. Спробуй ще раз.")

        payment_event_external_id = str(telegram_payment_charge_id or "").strip() or str(payload.external_payment_id)
        expires_at_iso = _iso_from_unix_utc(subscription_expiration_date)
        return await self.apply_payment_event(
            tg_user_id=int(tg_user_id),
            place_id=int(payload.place_id),
            tier=normalized_tier,
            provider=PAYMENT_PROVIDER_TELEGRAM_STARS,
            intent_external_payment_id=str(payload.external_payment_id),
            payment_external_id=payment_event_external_id,
            event_type="payment_succeeded",
            amount_stars=int(expected_amount),
            source=str(payload.source or "card"),
            currency="XTR",
            status="processed",
            expires_at=expires_at_iso,
            raw_payload_json=raw_payload_json
            or _to_json(
                {
                    "source": "telegram_stars_successful_payment",
                    "tg_user_id": int(tg_user_id),
                    "invoice_payload": str(invoice_payload),
                    "intent_external_payment_id": str(payload.external_payment_id),
                    "telegram_payment_charge_id": str(telegram_payment_charge_id or ""),
                    "provider_payment_charge_id": str(provider_payment_charge_id or ""),
                    "subscription_expiration_date": subscription_expiration_date,
                    "is_recurring": bool(is_recurring) if is_recurring is not None else None,
                    "is_first_recurring": bool(is_first_recurring) if is_first_recurring is not None else None,
                }
            ),
            audit_extra={
                "is_recurring": bool(is_recurring) if is_recurring is not None else None,
                "is_first_recurring": bool(is_first_recurring) if is_first_recurring is not None else None,
                "subscription_expiration_date": subscription_expiration_date,
                "provider_payment_charge_id": str(provider_payment_charge_id or ""),
            },
        )
