"""Business service abstraction and no-op implementation."""

from __future__ import annotations

from typing import Any, Protocol

from business.guards import is_business_feature_enabled


class BusinessService(Protocol):
    """Service contract used by main bot adapters."""

    async def enrich_places_for_main_bot(self, places: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return place list enriched with business metadata."""


class NoopBusinessService:
    """Fallback service when business mode is disabled."""

    async def enrich_places_for_main_bot(self, places: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return places


class BusinessServiceStub:
    """Placeholder service for future business features."""

    async def enrich_places_for_main_bot(self, places: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return places


def get_business_service() -> BusinessService:
    """Resolve enabled service by feature flag."""
    if not is_business_feature_enabled():
        return NoopBusinessService()
    return BusinessServiceStub()
