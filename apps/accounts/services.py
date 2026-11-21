"""Account-level business rules."""

from __future__ import annotations

from typing import Any

from allauth.mfa.models import Authenticator
from django.db import transaction

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


# --- GDPR: export and erasure (README §7.12) ----------------------------------------


def models_q_requester(user: User) -> Any:
    from django.db.models import Q

    return Q(requester=user) | Q(requester_email__iexact=user.email)


def export_user_data(user: User) -> dict[str, object]:
    """Everything PFM stores about a user, as JSON-serialisable data."""
    from allauth.account.models import EmailAddress

    from apps.booking.models import Booking
    from apps.core.models import JoinRequest
    from apps.resources.models import Authorization

    return {
        "profile": {
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "date_joined": user.date_joined.isoformat(),
            "last_login": user.last_login.isoformat() if user.last_login else None,
        },
        "email_addresses": [
            {"email": e.email, "verified": e.verified, "primary": e.primary}
            for e in EmailAddress.objects.filter(user=user)
        ],
        "memberships": [
            {"space": m.space.name, "role": m.get_role_display(), "since": m.created_at.isoformat()}
            for m in SpaceMembership.objects.filter(user=user).select_related("space")
        ],
        "clients": [c.name for c in user.clients.all()],  # type: ignore[attr-defined]
        "authorizations": [
            {
                "space": a.space.name,
                "category": a.category.name,
                "granted_on": a.granted_on.isoformat(),
                "expires_on": a.expires_on.isoformat() if a.expires_on else None,
                "active": a.is_active,
            }
            for a in Authorization.objects.filter(user=user).select_related("space", "category")
        ],
        "bookings": [
            {
                "space": b.space.name,
                "resource": b.resource.name,
                "start": b.start.isoformat(),
                "end": b.end.isoformat(),
                "title": b.title,
                "note": b.note,
                "status": b.status,
            }
            for b in Booking.objects.filter(user=user).select_related("space", "resource")
        ],
        "helpdesk": [
            {
                "space": t.space.name,
                "number": t.number,
                "subject": t.subject,
                "messages": [
                    {"at": m.created_at.isoformat(), "from": m.sender, "body": m.body}
                    for m in t.messages.all()
                ],
            }
            for t in _tickets(user)
        ],
        "join_requests": [
            {"space": j.space.name, "status": j.status, "message": j.message}
            for j in JoinRequest.objects.filter(user=user).select_related("space")
        ],
    }


def sole_admin_of(user: User) -> list[str]:
    """Names of the spaces where ``user`` is the only administrator."""
    from django.db.models import Count, Q

    from apps.core.models import Space

    return list(
        Space.objects.filter(memberships__user=user, memberships__role=Role.ADMIN)
        .annotate(admins=Count("memberships", filter=Q(memberships__role=Role.ADMIN)))
        .filter(admins=1)
        .values_list("name", flat=True)
    )


class ErasureRefused(Exception):
    pass


def _tickets(user: User) -> Any:
    from apps.helpdesk.models import Ticket

    return (
        Ticket.objects.filter(models_q_requester(user))
        .select_related("space")
        .prefetch_related("messages")
    )


@transaction.atomic
def anonymize(user: User) -> None:
    """Erase personal data but keep the records that must stay (bookings,
    invoices): the account becomes an inactive, anonymous placeholder."""
    from allauth.account.models import EmailAddress
    from allauth.mfa.models import Authenticator
    from django.contrib.sessions.models import Session

    from apps.booking.models import Booking, FeedToken
    from apps.core.models import JoinRequest

    if sole_admin_of(user):
        raise ErasureRefused("last administrator of a space")
    from allauth.socialaccount.models import SocialAccount

    from apps.helpdesk.models import Ticket, TicketMessage

    placeholder = f"deleted-{user.pk}"
    SocialAccount.objects.filter(user=user).delete()  # IdP claims (name, e-mail)
    erased = "[erased at the requester's request]"
    TicketMessage.objects.filter(incoming=True, sender__iexact=user.email).update(
        sender=placeholder, body=erased
    )
    Ticket.objects.filter(models_q_requester(user)).update(
        requester=None, requester_email=f"{placeholder}@invalid.invalid", subject=erased
    )
    Booking.objects.filter(user=user).update(title="", note="")
    Booking.history.filter(user=user).update(title="", note="")  # audit trail too
    SpaceMembership.objects.filter(user=user).delete()
    JoinRequest.objects.filter(user=user).delete()
    # Remove from the client side: simple_history tracks Client.users and
    # breaks when the change comes from the reverse (User) side.
    for client in list(user.clients.all()):  # type: ignore[attr-defined]
        client.users.remove(user)
    EmailAddress.objects.filter(user=user).delete()
    Authenticator.objects.filter(user=user).delete()
    FeedToken.objects.filter(user=user).delete()
    user.username = placeholder
    user.email = f"{placeholder}@invalid.invalid"
    user.first_name = user.last_name = ""
    user.is_active = user.is_staff = user.is_superuser = False
    user.set_unusable_password()
    user.save()
    # Log out every session of this user.
    for session in Session.objects.all().iterator():
        if session.get_decoded().get("_auth_user_id") == str(user.pk):
            session.delete()
