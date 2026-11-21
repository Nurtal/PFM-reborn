from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import SpaceScopedModel
from apps.resources.models import Resource


class Status(models.TextChoices):
    CONFIRMED = "confirmed", _("Confirmed")
    CANCELLED = "cancelled", _("Cancelled")


class Booking(SpaceScopedModel):
    """A reservation of a resource over ``[start, end)`` (UTC).

    Create and change bookings only through ``booking.services``: the overlap
    check and the write must happen in the same transaction.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    resource = models.ForeignKey(
        Resource, on_delete=models.PROTECT, related_name="bookings", verbose_name=_("resource")
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="bookings",
        verbose_name=_("booked for"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    start = models.DateTimeField(_("start"), db_index=True)
    end = models.DateTimeField(_("end"), db_index=True)
    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="bookings",
        verbose_name=_("client"),
    )
    package = models.ForeignKey(
        "clients.Package",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="bookings",
        verbose_name=_("package"),
    )
    title = models.CharField(_("title"), max_length=200, blank=True)
    note = models.TextField(_("note"), blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CONFIRMED)
    billed_externally = models.BooleanField(
        default=False, help_text="Already invoiced outside PFM-NG (e.g. by the legacy system)."
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["start"]
        verbose_name = _("booking")
        verbose_name_plural = _("bookings")
        indexes = [models.Index(fields=["resource", "status", "start", "end"])]
        constraints = [
            models.CheckConstraint(condition=Q(end__gt=F("start")), name="booking_end_after_start"),
        ]

    def __str__(self) -> str:
        return f"{self.resource} {self.start:%Y-%m-%d %H:%M} ({self.user})"

    @property
    def is_cancelled(self) -> bool:
        return self.status == Status.CANCELLED


class Closure(SpaceScopedModel):
    """A blocked slot (maintenance, holidays). ``resource=None`` blocks the whole area."""

    resource = models.ForeignKey(
        Resource,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="closures",
        verbose_name=_("resource"),
    )
    area = models.ForeignKey(
        "resources.Area",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="closures",
        verbose_name=_("area"),
    )
    start = models.DateTimeField(_("start"), db_index=True)
    end = models.DateTimeField(_("end"), db_index=True)
    reason = models.CharField(_("reason"), max_length=200)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["start"]
        verbose_name = _("closure")
        verbose_name_plural = _("closures")
        constraints = [
            models.CheckConstraint(condition=Q(end__gt=F("start")), name="closure_end_after_start"),
            models.CheckConstraint(
                condition=Q(resource__isnull=False) | Q(area__isnull=False),
                name="closure_has_target",
            ),
        ]

    def __str__(self) -> str:
        return self.reason


def _new_token() -> str:
    import secrets

    return secrets.token_urlsafe(24)


class FeedToken(models.Model):
    """Secret part of a user's iCal feed URL. Regenerate it to revoke the old URL."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="feed_token"
    )
    token = models.CharField(max_length=64, unique=True, default=_new_token)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"iCal feed of {self.user}"

    def regenerate(self) -> None:
        self.token = _new_token()
        self.save(update_fields=["token"])
