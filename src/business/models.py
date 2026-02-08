"""Business domain models (MVP skeleton)."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class BusinessOwner:
    business_id: int
    tg_user_id: int
    role: str
    created_at: datetime | None = None


@dataclass(slots=True)
class BusinessSubscription:
    business_id: int
    tier: str
    status: str
    starts_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass(slots=True)
class BusinessPlacePolicy:
    place_id: int
    is_verified: bool = False
    verified_tier: str | None = None
