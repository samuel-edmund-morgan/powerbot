"""Business domain models used by business module."""

from dataclasses import dataclass


@dataclass(slots=True)
class BusinessOwner:
    id: int
    place_id: int
    tg_user_id: int
    role: str
    status: str
    created_at: str
    approved_at: str | None = None
    approved_by: int | None = None


@dataclass(slots=True)
class BusinessSubscription:
    place_id: int
    tier: str
    status: str
    starts_at: str | None = None
    expires_at: str | None = None


@dataclass(slots=True)
class BusinessClaimToken:
    id: int
    place_id: int
    token: str
    status: str
    attempts_left: int
    created_at: str
    expires_at: str
    created_by: int | None = None
    used_at: str | None = None
    used_by: int | None = None


@dataclass(slots=True)
class BusinessPlacePolicy:
    place_id: int
    business_enabled: bool = False
    is_verified: bool = False
    verified_tier: str | None = None
    verified_until: str | None = None
