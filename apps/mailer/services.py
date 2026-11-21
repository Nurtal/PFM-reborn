"""Group e-mails to the members of a platform."""

from __future__ import annotations

import datetime as dt

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.booking.models import Booking
from apps.booking.models import Status as BookingStatus
from apps.mailer.models import AUDIENCE_MIN_ROLE, Audience, Mailing

RECENT = dt.timedelta(days=180)


def recipients(mailing: Mailing) -> list[str]:
    """Distinct e-mail addresses of the active members targeted by ``mailing``."""
    # Space and role must be checked in ONE filter() call: separate calls on the
    # multi-valued memberships relation would match a role held in another space.
    if mailing.audience == Audience.RESOURCE:
        users = User.objects.filter(memberships__space_id=mailing.space_id)
    else:
        users = User.objects.filter(
            memberships__space_id=mailing.space_id,
            memberships__role__gte=AUDIENCE_MIN_ROLE[Audience(mailing.audience)],
        )
    users = users.filter(is_active=True).exclude(email="")
    if mailing.audience == Audience.RESOURCE:
        booked = Booking.objects.filter(
            resource=mailing.resource,
            status=BookingStatus.CONFIRMED,
            start__gte=timezone.now() - RECENT,
        ).values("user_id")
        users = users.filter(pk__in=booked)
    return sorted(set(users.values_list("email", flat=True)))


def send(mailing: Mailing) -> Mailing:
    """Queue the mailing; the Huey task sends one message per recipient (no Cc leak)."""
    from apps.mailer import tasks

    mailing.recipients_count = len(recipients(mailing))
    mailing.save(update_fields=["recipients_count"])
    transaction.on_commit(lambda: tasks.deliver(mailing.pk))
    return mailing
