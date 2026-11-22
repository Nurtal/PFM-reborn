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
    project = models.ForeignKey(
        "services.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name=_("project"),
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
    billed_externally = models.BooleanField(
        default=False, help_text="Already invoiced outside PFM-NG (e.g. by the legacy system)."
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["performed_on", "pk"]

    def __str__(self) -> str:
        return f"{self.quantity} × {self.item}"


class ProjectStatus(models.TextChoices):
    PLANNED = "planned", _("Planned")
    ACTIVE = "active", _("In progress")
    CLOSED = "closed", _("Closed")


class Project(SpaceScopedModel):
    """A long-running piece of work for a client, grouping orders (legacy "projet")."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(_("name"), max_length=200)
    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.PROTECT,
        related_name="projects",
        verbose_name=_("client"),
    )
    lead = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("lead"),
    )
    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("planned end"))
    status = models.CharField(
        _("status"), max_length=10, choices=ProjectStatus.choices, default=ProjectStatus.PLANNED
    )
    description = models.TextField(_("description"), blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["start_date", "name"]
        verbose_name = _("project")
        verbose_name_plural = _("projects")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="project_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Cabinet(SpaceScopedModel):
    """Storage unit (freezer, cabinet) holding shelves (legacy "armoire")."""

    name = models.CharField(_("name"), max_length=120)
    location = models.CharField(_("location"), max_length=200, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("cabinet")
        verbose_name_plural = _("cabinets")
        constraints = [
            models.UniqueConstraint(fields=["space", "name"], name="unique_cabinet_name"),
        ]

    def __str__(self) -> str:
        return self.name


class Shelf(models.Model):
    cabinet = models.ForeignKey(Cabinet, on_delete=models.CASCADE, related_name="shelves")
    name = models.CharField(_("name"), max_length=120)
    capacity = models.PositiveIntegerField(_("capacity"), null=True, blank=True)

    class Meta:
        ordering = ["cabinet", "name"]
        verbose_name = _("shelf")
        verbose_name_plural = _("shelves")
        constraints = [
            models.UniqueConstraint(fields=["cabinet", "name"], name="unique_shelf_name"),
        ]

    def __str__(self) -> str:
        return f"{self.cabinet} / {self.name}"


class StockEntry(SpaceScopedModel):
    """Client material stored on a shelf, from deposit to withdrawal."""

    shelf = models.ForeignKey(
        Shelf, on_delete=models.PROTECT, related_name="entries", verbose_name=_("shelf")
    )
    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="stock", verbose_name=_("client")
    )
    label = models.CharField(_("label"), max_length=200)
    quantity = models.PositiveIntegerField(_("quantity"), default=1)
    stored_on = models.DateField(_("stored on"), default=timezone.localdate)
    removed_on = models.DateField(_("removed on"), null=True, blank=True)
    note = models.CharField(_("note"), max_length=200, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-stored_on"]
        verbose_name = _("stock entry")
        verbose_name_plural = _("stock entries")

    def __str__(self) -> str:
        return self.label
