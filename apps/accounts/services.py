"""Account-level business rules."""

from __future__ import annotations

from allauth.mfa.models import Authenticator

from apps.accounts.models import User
from apps.core.models import Role, SpaceMembership

STRONG_MFA_TYPES = (Authenticator.Type.TOTP, Authenticator.Type.WEBAUTHN)


def requires_mfa(user: User) -> bool:
    """Superusers and space administrators must use a second factor."""
    if user.is_superuser or user.is_staff:
        return True
    return bool(SpaceMembership.objects.filter(user=user, role=Role.ADMIN).exists())


def has_mfa(user: User) -> bool:
    return bool(Authenticator.objects.filter(user=user, type__in=STRONG_MFA_TYPES).exists())
