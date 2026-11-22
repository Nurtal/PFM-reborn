"""Bookable resources (equipment, rooms), their areas, categories and the
authorizations ("habilitations") users need before booking some of them."""

from __future__ import annotations

import datetime as dt
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import SpaceQuerySet, SpaceScopedModel, hex_color

ALL_WEEKDAYS = 0b1111111
WORKING_WEEKDAYS = 0b0011111  # Monday (bit 0) .. Friday (bit 4)


class Area(SpaceScopedModel):
    """A zone (room, floor, lab) grouping resources that share opening hours.

    ``day_end`` of 00:00 means midnight at the end of the day, so 00:00-00:00
    with every weekday open is a 24/7 schedule.
    """

    name = models.CharField(_("name"), max_length=120)
    is_restricted = models.BooleanField(
        _("restricted"), default=False, help_text=_("Only managers can see this area.")
    )
    order = models.PositiveSmallIntegerField(_("display order"), default=0)

    open_weekdays = models.PositiveSmallIntegerField(
        _("open days"),
        default=WORKING_WEEKDAYS,
        validators=[MaxValueValidator(ALL_WEEKDAYS)],
        help_text=_("Bit mask, Monday = 1, Tuesday = 2, ..., Sunday = 64."),
    )
    day_start = models.TimeField(_("opening time"), default=dt.time(8, 0))
    day_end = models.TimeField(_("closing time"), default=dt.time(19, 0))
    slot_minutes = models.PositiveSmallIntegerField(
        _("slot size (minutes)"),
        default=30,
        validators=[MinValueValidator(5), MaxValueValidator(24 * 60)],
    )

    class Meta:
        ordering = ["order", "name"]
        verbose_name = _("area")
        verbose_name_plural = _("areas")
        constraints = [
            models.UniqueConstraint(fields=["space", "name"], name="unique_area_name"),
        ]

    def __str__(self) -> str:
        return self.name

    def is_open_on(self, weekday: int) -> bool:
        return bool(self.open_weekdays & (1 << weekday))

    @property
    def open_weekday_list(self) -> list[int]:
        return [d for d in range(7) if self.is_open_on(d)]

    def clean(self) -> None:
        if self.day_end != dt.time(0) and self.day_end <= self.day_start:
            raise ValidationError({"day_end": _("Closing time must be after opening time.")})


class ResourceCategory(SpaceScopedModel):
    """Resources of a category share authorizations (legacy "resource category")."""

    name = models.CharField(_("name"), max_length=120)

    class Meta:
        ordering = ["name"]
        verbose_name = _("resource category")
        verbose_name_plural = _("resource categories")
        constraints = [
            models.UniqueConstraint(fields=["space", "name"], name="unique_category_name"),
        ]

    def __str__(self) -> str:
        return self.name


class Access(models.TextChoices):
    """Who may book a resource (legacy ``bk_access``)."""

    USERS = "users", _("Every platform user")
    AUTHORIZED = "authorized", _("Authorized users only")
    MANAGERS = "managers", _("Managers only")


class Resource(SpaceScopedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(_("name"), max_length=120)
    brand = models.CharField(_("brand"), max_length=120, blank=True)
    model = models.CharField(_("model"), max_length=120, blank=True)
    description = models.TextField(_("description"), blank=True)
    category = models.ForeignKey(
        ResourceCategory,
        on_delete=models.PROTECT,
        related_name="resources",
        verbose_name=_("category"),
    )
    area = models.ForeignKey(
        Area, on_delete=models.PROTECT, related_name="resources", verbose_name=_("area")
    )
    color = models.CharField(_("colour"), max_length=7, default="#4f46e5", validators=[hex_color])
    order = models.PositiveSmallIntegerField(_("display order"), default=0)
    is_active = models.BooleanField(_("active"), default=True)
    managers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="managed_resources",
        verbose_name=_("managers"),
    )

    # Booking restrictions. Space managers are not bound by them.
    access = models.CharField(
        _("who can book"), max_length=12, choices=Access.choices, default=Access.USERS
    )
    min_duration_minutes = models.PositiveIntegerField(_("minimum duration (minutes)"), default=0)
    max_duration_minutes = models.PositiveIntegerField(
        _("maximum duration (minutes)"), null=True, blank=True
    )
    max_bookings_per_day = models.PositiveSmallIntegerField(
        _("maximum bookings per user and day"), null=True, blank=True
    )
    max_advance_days = models.PositiveSmallIntegerField(
        _("bookable up to (days ahead)"), null=True, blank=True
    )
    edit_deadline_hours = models.PositiveSmallIntegerField(
        _("users can change or cancel until (hours before start)"), default=0
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["order", "name"]
        verbose_name = _("resource")
        verbose_name_plural = _("resources")
        constraints = [
            models.UniqueConstraint(fields=["space", "name"], name="unique_resource_name"),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        errors = {}
        for field in ("category", "area"):
            related = getattr(self, f"{field}_id") and getattr(self, field)
            if related and self.space_id and related.space_id != self.space_id:
                errors[field] = _("Must belong to the same platform.")
        if (
            self.max_duration_minutes is not None
            and self.max_duration_minutes < self.min_duration_minutes
        ):
            errors["max_duration_minutes"] = _("Must be at least the minimum duration.")
        if errors:
            raise ValidationError(errors)


class AuthorizationQuerySet(SpaceQuerySet):
    def valid(self, on: dt.date | None = None) -> AuthorizationQuerySet:
        on = on or timezone.localdate()
        return self.filter(is_active=True, granted_on__lte=on).filter(
            models.Q(expires_on__isnull=True) | models.Q(expires_on__gte=on)
        )


class Authorization(SpaceScopedModel):
    """A user is trained and allowed to book resources of a category."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="authorizations"
    )
    category = models.ForeignKey(
        ResourceCategory,
        on_delete=models.CASCADE,
        related_name="authorizations",
        verbose_name=_("category"),
    )
    granted_on = models.DateField(_("granted on"), default=timezone.localdate)
    expires_on = models.DateField(_("expires on"), null=True, blank=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("trainer"),
    )
    is_active = models.BooleanField(_("active"), default=True)
    comment = models.CharField(_("comment"), max_length=255, blank=True)

    objects = AuthorizationQuerySet.as_manager()  # type: ignore[misc]
    history = HistoricalRecords()

    class Meta:
        ordering = ["-granted_on"]
        verbose_name = _("authorization")
        verbose_name_plural = _("authorizations")
        constraints = [
            models.UniqueConstraint(fields=["user", "category"], name="unique_authorization"),
        ]

    def __str__(self) -> str:
        return f"{self.user} — {self.category}"
