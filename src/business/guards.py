"""Feature-flag guards for business mode."""

from config import is_business_mode_enabled, is_business_bot_enabled


def is_business_feature_enabled() -> bool:
    """Main bot business UI/logic gate."""
    return is_business_mode_enabled()


def is_business_bot_configured() -> bool:
    """Separate business bot should run only when fully configured."""
    return is_business_bot_enabled()


def is_business_subscription_lifecycle_enabled() -> bool:
    """Subscription lifecycle must run when either business mode or businessbot is enabled."""
    return is_business_mode_enabled() or is_business_bot_enabled()
