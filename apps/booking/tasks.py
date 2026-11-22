"""Booking e-mails, sent by the Huey consumer."""

from __future__ import annotations

import logging
import zoneinfo

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import translation
from huey.contrib.djhuey import db_task

logger = logging.getLogger(__name__)


@db_task(retries=3, retry_delay=60)
def notify_booking(booking_id: int, event: str) -> None:
    from apps.booking.models import Booking

    booking = (
        Booking.objects.select_related("resource", "space", "user", "created_by")
        .filter(pk=booking_id)
        .first()
    )
    if booking is None or not booking.user.email:
        return
    tz = zoneinfo.ZoneInfo(booking.space.timezone)
    context = {
        "booking": booking,
        "event": event,
        "start": booking.start.astimezone(tz),
        "end": booking.end.astimezone(tz),
        "url": f"{settings.PFM_URL}/s/{booking.space.slug}/booking/{booking.resource.public_id}/",
    }
    with translation.override(settings.LANGUAGE_CODE):
        subject = render_to_string("booking/email/subject.txt", context).strip()
        body = render_to_string("booking/email/body.txt", context)
    send_mail(subject, body, None, [booking.user.email])
    logger.info("Booking %s: %s mail sent", booking.public_id, event)
