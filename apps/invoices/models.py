"""Invoices and credit notes.

Rules (README §6): an issued invoice is immutable, its lines are frozen copies
of the prices at issue time, corrections go through a credit note, and numbers
are sequential per space and kind, without gaps (allocated only at issue).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import Space, SpaceScopedModel

MONEY: dict[str, Any] = {"max_digits": 12, "decimal_places": 2}


class InvoiceSettings(models.Model):
    """Per-space issuer details and numbering."""

    space = models.OneToOneField(Space, on_delete=models.CASCADE, related_name="invoice_settings")
    number_prefix = models.CharField(_("invoice prefix"), max_length=10, default="F")
    credit_prefix = models.CharField(_("credit note prefix"), max_length=10, default="A")
    quote_prefix = models.CharField(_("quote prefix"), max_length=10, default="D")
    quote_validity_days = models.PositiveSmallIntegerField(_("quote validity (days)"), default=30)
    vat_rate = models.DecimalField(
        _("VAT rate (%)"),
        max_digits=5,
        decimal_places=2,
        default=Decimal("20.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    payment_terms_days = models.PositiveSmallIntegerField(_("payment terms (days)"), default=30)
    issuer_name = models.CharField(_("issuer"), max_length=200, blank=True)
    issuer_address = models.TextField(_("issuer address"), blank=True)
    legal_mentions = models.TextField(
        _("legal mentions"), blank=True, help_text=_("SIRET, VAT number, legal form...")
    )
    payment_details = models.TextField(
        _("payment details"), blank=True, help_text=_("Bank details, payment instructions.")
    )
    footer = models.TextField(_("footer"), blank=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("invoicing settings")
        verbose_name_plural = _("invoicing settings")

    def __str__(self) -> str:
        return f"Invoicing settings of {self.space}"

    @classmethod
    def for_space(cls, space: Space) -> InvoiceSettings:
        obj, _created = cls.objects.get_or_create(space=space, defaults={"issuer_name": space.name})
        return obj


class Kind(models.TextChoices):
    INVOICE = "invoice", _("Invoice")
    CREDIT = "credit", _("Credit note")
    QUOTE = "quote", _("Quote")


# Kinds that are accounting documents (revenue, exports, re-invoicing rules).
ACCOUNTING_KINDS = (Kind.INVOICE, Kind.CREDIT)


class QuoteStatus(models.TextChoices):
    PENDING = "pending", _("Awaiting answer")
    ACCEPTED = "accepted", _("Accepted")
    REFUSED = "refused", _("Refused")


class Status(models.TextChoices):
    DRAFT = "draft", _("Draft")
    ISSUED = "issued", _("Issued")


class NumberCounter(models.Model):
    """Last number used per space, kind and year. Locked while issuing."""

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    year = models.PositiveSmallIntegerField()
    last = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["space", "kind", "year"], name="unique_counter"),
        ]

    def __str__(self) -> str:
        return f"{self.space_id}/{self.kind}/{self.year}: {self.last}"


class ImmutableInvoiceError(Exception):
    pass


# Fields that may still change once an invoice is issued.
MUTABLE_AFTER_ISSUE = {"paid_at", "pdf", "updated_at", "quote_status", "converted_to"}


def _pdf_path(instance: Invoice, filename: str) -> str:
    return f"invoices/{instance.space_id}/{instance.public_id}.pdf"


class Invoice(SpaceScopedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.INVOICE)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    # NULL until issued: the unique constraint only applies to real numbers.
    number = models.CharField(_("number"), max_length=30, null=True, blank=True)  # noqa: DJ001

    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.PROTECT,
        related_name="invoices",
        verbose_name=_("client"),
    )
    period_start = models.DateField(_("period start"))
    period_end = models.DateField(_("period end"))
    credited_invoice = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="credit_note",
        verbose_name=_("credited invoice"),
    )
    note = models.TextField(_("note"), blank=True)
    quote_status = models.CharField(
        _("quote status"), max_length=10, choices=QuoteStatus.choices, default=QuoteStatus.PENDING
    )
    converted_to = models.OneToOneField(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="from_quote",
        verbose_name=_("invoice created from this quote"),
    )

    # Frozen at issue time.
    issue_date = models.DateField(_("issue date"), null=True, blank=True)
    due_date = models.DateField(_("due date"), null=True, blank=True)
    client_name = models.CharField(max_length=200, blank=True)
    client_address = models.TextField(blank=True)
    client_reference = models.CharField(max_length=100, blank=True)
    currency = models.CharField(max_length=3, default="EUR")
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    total_excl_tax = models.DecimalField(**MONEY, default=Decimal("0"))
    total_tax = models.DecimalField(**MONEY, default=Decimal("0"))
    total_incl_tax = models.DecimalField(**MONEY, default=Decimal("0"))
    pdf = models.FileField(upload_to=_pdf_path, blank=True)

    paid_at = models.DateField(_("paid on"), null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-issue_date", "-number", "-created_at"]
        verbose_name = _("invoice")
        verbose_name_plural = _("invoices")
        constraints = [
            models.UniqueConstraint(
                fields=["space", "number"],
                condition=models.Q(number__isnull=False),
                name="unique_invoice_number",
            ),
            models.CheckConstraint(
                condition=models.Q(status="draft") | models.Q(number__isnull=False),
                name="issued_invoice_has_number",
            ),
        ]

    def __str__(self) -> str:
        return self.number or f"Draft {self.client}"

    @property
    def is_issued(self) -> bool:
        return self.status == Status.ISSUED

    @property
    def is_quote(self) -> bool:
        return self.kind == Kind.QUOTE

    @property
    def is_credited(self) -> bool:
        return hasattr(self, "credit_note") and self.credit_note is not None

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk:
            stored = Invoice.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored == Status.ISSUED:
                fields = kwargs.get("update_fields")
                if fields is None or not set(fields) <= MUTABLE_AFTER_ISSUE:
                    raise ImmutableInvoiceError(f"Invoice {self.number} is issued and immutable")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        if self.is_issued:
            raise ImmutableInvoiceError("Issued invoices cannot be deleted; issue a credit note")
        return super().delete(*args, **kwargs)


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveIntegerField(default=0)
    description = models.CharField(_("description"), max_length=300)
    quantity = models.DecimalField(_("quantity"), max_digits=10, decimal_places=2)
    unit = models.CharField(_("unit"), max_length=20, blank=True)
    unit_price = models.DecimalField(_("unit price"), **MONEY)
    amount = models.DecimalField(_("amount"), **MONEY)
    booking = models.ForeignKey(
        "booking.Booking",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoice_lines",
    )
    order_line = models.ForeignKey(
        "services.OrderLine",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoice_lines",
    )

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self) -> str:
        return self.description

    def save(self, *args: Any, **kwargs: Any) -> None:
        self._check_mutable()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        self._check_mutable()
        return super().delete(*args, **kwargs)

    def _check_mutable(self) -> None:
        if Invoice.objects.filter(pk=self.invoice_id, status=Status.ISSUED).exists():
            raise ImmutableInvoiceError("Lines of an issued invoice are frozen")

    def clean(self) -> None:
        if self.quantity is not None and self.quantity == 0:
            raise ValidationError({"quantity": _("Quantity cannot be zero.")})
