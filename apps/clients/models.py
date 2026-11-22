"""Clients (billing accounts), pricing grids and resource prices."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import SpaceScopedModel
from apps.resources.models import ALL_WEEKDAYS, Resource

WEEKEND = 0b1100000  # Saturday (bit 5) and Sunday (bit 6)
MONEY: dict[str, Any] = {
    "max_digits": 12,
    "decimal_places": 2,
    "validators": [MinValueValidator(0)],
}


class PricingGrid(SpaceScopedModel):
    """A tariff family (academic, industrial...) and its night/weekend rules."""

    name = models.CharField(_("name"), max_length=120)
    order = models.PositiveSmallIntegerField(_("display order"), default=0)

    night_rate_enabled = models.BooleanField(_("night rate"), default=False)
    night_start = models.TimeField(_("night starts at"), default=dt.time(19, 0))
    night_end = models.TimeField(_("night ends at"), default=dt.time(8, 0))
    weekend_rate_enabled = models.BooleanField(_("weekend rate"), default=False)
    weekend_days = models.PositiveSmallIntegerField(
        _("weekend days"), default=WEEKEND, validators=[MaxValueValidator(ALL_WEEKDAYS)]
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["order", "name"]
        verbose_name = _("pricing grid")
        verbose_name_plural = _("pricing grids")
        constraints = [
            models.UniqueConstraint(fields=["space", "name"], name="unique_grid_name"),
        ]

    def __str__(self) -> str:
        return self.name


class Client(SpaceScopedModel):
    """Who pays: a lab, a team, a company. Users book on behalf of a client."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(_("name"), max_length=200)
    organization = models.CharField(_("organization"), max_length=200, blank=True)
    pricing_grid = models.ForeignKey(
        PricingGrid,
        on_delete=models.PROTECT,
        related_name="clients",
        verbose_name=_("pricing grid"),
    )
    contact_name = models.CharField(_("contact"), max_length=200, blank=True)
    email = models.EmailField(_("email"), blank=True)
    phone = models.CharField(_("phone"), max_length=40, blank=True)
    invoice_address = models.TextField(_("invoice address"), blank=True)
    reference = models.CharField(
        _("accounting reference"),
        max_length=100,
        blank=True,
        help_text=_("Purchase order, cost centre..."),
    )
    users = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="clients", verbose_name=_("users")
    )
    is_active = models.BooleanField(_("active"), default=True)

    history = HistoricalRecords(m2m_fields=[users])

    class Meta:
        ordering = ["name"]
        verbose_name = _("client")
        verbose_name_plural = _("clients")
        constraints = [
            models.UniqueConstraint(fields=["space", "name"], name="unique_client_name"),
        ]

    def __str__(self) -> str:
        return self.name


class ResourcePrice(SpaceScopedModel):
    """Hourly rates of a resource for one grid."""

    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="prices")
    grid = models.ForeignKey(PricingGrid, on_delete=models.CASCADE, related_name="resource_prices")
    day_rate = models.DecimalField(_("hourly rate"), **MONEY)
    night_rate = models.DecimalField(_("night hourly rate"), null=True, blank=True, **MONEY)
    weekend_rate = models.DecimalField(_("weekend hourly rate"), null=True, blank=True, **MONEY)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("resource price")
        verbose_name_plural = _("resource prices")
        constraints = [
            models.UniqueConstraint(fields=["resource", "grid"], name="unique_resource_price"),
        ]

    def __str__(self) -> str:
        return f"{self.resource} / {self.grid}"


class Package(SpaceScopedModel):
    """A fixed-price block of time on a resource (legacy "forfait")."""

    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="packages")
    name = models.CharField(_("name"), max_length=120)
    duration_minutes = models.PositiveIntegerField(
        _("duration (minutes)"), validators=[MinValueValidator(1)]
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["resource", "duration_minutes"]
        verbose_name = _("package")
        verbose_name_plural = _("packages")

    def __str__(self) -> str:
        return self.name


class PackagePrice(SpaceScopedModel):
    package = models.ForeignKey(Package, on_delete=models.CASCADE, related_name="prices")
    grid = models.ForeignKey(PricingGrid, on_delete=models.CASCADE, related_name="package_prices")
    price = models.DecimalField(_("price"), **MONEY)

    history = HistoricalRecords()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["package", "grid"], name="unique_package_price"),
        ]

    def __str__(self) -> str:
        return f"{self.package} / {self.grid}"
