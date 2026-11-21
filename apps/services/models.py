"""Services ("prestations") sold by a platform, and client orders of them."""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import SpaceScopedModel

MONEY: dict[str, Any] = {
    "max_digits": 12,
    "decimal_places": 2,
    "validators": [MinValueValidator(0)],
}


class ServiceItem(SpaceScopedModel):
    name = models.CharField(_("name"), max_length=200)
    unit = models.CharField(
        _("unit"), max_length=30, default="u", help_text=_("sample, hour, slide...")
    )
    description = models.TextField(_("description"), blank=True)
    order = models.PositiveSmallIntegerField(_("display order"), default=0)
    is_active = models.BooleanField(_("active"), default=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["order", "name"]
        verbose_name = _("service")
        verbose_name_plural = _("services")
        constraints = [
            models.UniqueConstraint(fields=["space", "name"], name="unique_service_name"),
        ]

    def __str__(self) -> str:
        return self.name


class ServicePrice(SpaceScopedModel):
    item = models.ForeignKey(ServiceItem, on_delete=models.CASCADE, related_name="prices")
    grid = models.ForeignKey(
        "clients.PricingGrid", on_delete=models.CASCADE, related_name="service_prices"
    )
    price = models.DecimalField(_("unit price"), **MONEY)

    history = HistoricalRecords()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["item", "grid"], name="unique_service_price"),
        ]

    def __str__(self) -> str:
        return f"{self.item} / {self.grid}"


class OrderStatus(models.TextChoices):
    OPEN = "open", _("Open")
    DONE = "done", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")


class Order(SpaceScopedModel):
    """A client's request for services (legacy "commande"), filled over time."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="orders", verbose_name=_("client")
    )
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("requested by"),
    )
    title = models.CharField(_("title"), max_length=200)
    note = models.TextField(_("note"), blank=True)
    status = models.CharField(
        _("status"), max_length=10, choices=OrderStatus.choices, default=OrderStatus.OPEN
    )
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("order")
        verbose_name_plural = _("orders")

    def __str__(self) -> str:
        return self.title


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(
        ServiceItem, on_delete=models.PROTECT, related_name="+", verbose_name=_("service")
    )
    quantity = models.DecimalField(
        _("quantity"), max_digits=10, decimal_places=2, validators=[MinValueValidator(0)]
    )
    performed_on = models.DateField(_("performed on"), default=timezone.localdate)
    comment = models.CharField(_("comment"), max_length=200, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["performed_on", "pk"]

    def __str__(self) -> str:
        return f"{self.quantity} × {self.item}"
