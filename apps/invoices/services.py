"""Invoicing business rules: drafts from bookings, issue, credit notes."""

from __future__ import annotations

import datetime as dt
import zoneinfo
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.booking.models import Booking
from apps.booking.models import Status as BookingStatus
from apps.clients.models import Client
from apps.clients.pricing import Period, service_amount
from apps.clients.services import booking_quote
from apps.core.models import Space
from apps.invoices.models import (
    Invoice,
    InvoiceLine,
    InvoiceSettings,
    Kind,
    NumberCounter,
    QuoteStatus,
    Status,
)
from apps.services.models import OrderLine, OrderStatus
from apps.services.services import unit_price

CENT = Decimal("0.01")


class InvoicingError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _period_bounds(space: Space, start: dt.date, end: dt.date) -> tuple[dt.datetime, dt.datetime]:
    tz = zoneinfo.ZoneInfo(space.timezone)
    return (
        dt.datetime.combine(start, dt.time(0), tzinfo=tz),
        dt.datetime.combine(end + dt.timedelta(days=1), dt.time(0), tzinfo=tz),
    )


def billable_bookings(
    space: Space, client: Client | None, start: dt.date, end: dt.date
) -> QuerySet[Booking]:
    """Confirmed bookings ending in the period that no live invoice covers yet.

    A booking stays covered by a draft or by an issued invoice, unless that
    invoice was cancelled by a credit note: then it can be billed again.
    """
    lo, hi = _period_bounds(space, start, end)
    covered = InvoiceLine.objects.filter(
        invoice__kind=Kind.INVOICE, invoice__credit_note__isnull=True, booking__isnull=False
    ).values("booking_id")
    qs = Booking.objects.filter(
        space=space,
        status=BookingStatus.CONFIRMED,
        client__isnull=False,
        end__gt=lo,
        end__lte=hi,
    ).exclude(pk__in=covered)
    if client is not None:
        qs = qs.filter(client=client)
    return qs.select_related("resource", "user", "client__pricing_grid", "package").order_by(
        "start"
    )


def billable_order_lines(
    space: Space, client: Client | None, start: dt.date, end: dt.date
) -> QuerySet[OrderLine]:
    """Lines of open or completed orders performed in the period, not billed yet."""
    covered = InvoiceLine.objects.filter(
        invoice__kind=Kind.INVOICE, invoice__credit_note__isnull=True, order_line__isnull=False
    ).values("order_line_id")
    qs = OrderLine.objects.filter(
        order__space=space,
        order__status__in=[OrderStatus.OPEN, OrderStatus.DONE],
        performed_on__gte=start,
        performed_on__lte=end,
    ).exclude(pk__in=covered)
    if client is not None:
        qs = qs.filter(order__client=client)
    return qs.select_related("order__client", "item").order_by("performed_on", "pk")


def _order_line(line: OrderLine) -> InvoiceLine | None:
    price = unit_price(line.item, line.order.client)
    if price is None:
        return None
    when = date_format(line.performed_on, "SHORT_DATE_FORMAT")
    description = f"{line.item.name} — {line.order.title} — {when}"
    if line.comment:
        description += f" — {line.comment}"
    return InvoiceLine(
        description=description[:300],
        quantity=line.quantity,
        unit=line.item.unit[:20],
        unit_price=price,
        amount=service_amount(price, line.quantity),
        order_line=line,
    )


PERIOD_LABELS = {
    Period.DAY: "",
    Period.NIGHT: " · nuit",
    Period.WEEKEND: " · week-end",
    Period.PACKAGE: "",
}


def _describe(booking: Booking) -> str:
    tz = zoneinfo.ZoneInfo(booking.space.timezone)
    start, end = booking.start.astimezone(tz), booking.end.astimezone(tz)
    when = f"{date_format(start, 'SHORT_DATE_FORMAT')} {start:%H:%M}–{end:%H:%M}"
    if start.date() != end.date():
        when = (
            f"{date_format(start, 'SHORT_DATE_FORMAT')} {start:%H:%M} – "
            f"{date_format(end, 'SHORT_DATE_FORMAT')} {end:%H:%M}"
        )
    who = booking.user.get_full_name() or booking.user.username
    return f"{booking.resource.name} — {when} — {who}"


def _booking_lines(booking: Booking) -> list[InvoiceLine] | None:
    quote = booking_quote(
        booking.resource, booking.client, booking.start, booking.end, booking.package
    )
    if quote is None or not quote.lines:
        return None
    base = _describe(booking)
    lines = []
    for q in quote.lines:
        if q.period == Period.PACKAGE:
            label = f"{base} · {_('package')} {booking.package.name if booking.package else ''}"
            lines.append(
                InvoiceLine(
                    description=label[:300],
                    quantity=Decimal(1),
                    unit=_("package"),
                    unit_price=q.unit_price,
                    amount=q.amount,
                    booking=booking,
                )
            )
        else:
            lines.append(
                InvoiceLine(
                    description=f"{base}{PERIOD_LABELS[q.period]}"[:300],
                    quantity=_money(Decimal(q.minutes) / Decimal(60)),
                    unit="h",
                    unit_price=q.unit_price,
                    amount=q.amount,
                    booking=booking,
                )
            )
    return lines


@dataclass
class DraftResult:
    invoice: Invoice | None
    unpriced: list[Booking | OrderLine] = field(default_factory=list)


@transaction.atomic
def create_draft(
    actor: User, space: Space, client: Client, start: dt.date, end: dt.date
) -> DraftResult:
    """Draft invoice of a client's uninvoiced bookings over ``[start, end]``.

    Bookings without a price are left out and reported, never billed at 0.
    """
    if client.space_id != space.pk:
        raise InvoicingError(_("This client belongs to another platform."))
    if end < start:
        raise InvoicingError(_("The period end must be after its start."))
    lines: list[InvoiceLine] = []
    unpriced: list[Booking | OrderLine] = []
    for booking in billable_bookings(space, client, start, end):
        booking_lines = _booking_lines(booking)
        if booking_lines is None:
            unpriced.append(booking)
        else:
            lines += booking_lines
    for order_line in billable_order_lines(space, client, start, end):
        invoice_line = _order_line(order_line)
        if invoice_line is None:
            unpriced.append(order_line)
        else:
            lines.append(invoice_line)
    if not lines:
        return DraftResult(None, unpriced)
    invoice = Invoice.objects.create(
        space=space,
        client=client,
        period_start=start,
        period_end=end,
        currency=space.currency,
        created_by=actor,
    )
    for position, line in enumerate(lines):
        line.invoice = invoice
        line.position = position
    InvoiceLine.objects.bulk_create(lines)
    return DraftResult(invoice, unpriced)


def create_drafts_for_period(
    actor: User, space: Space, start: dt.date, end: dt.date
) -> list[DraftResult]:
    """Monthly run: one draft per client with billable bookings."""
    client_ids = set(
        billable_bookings(space, None, start, end).values_list("client_id", flat=True)
    ) | set(
        billable_order_lines(space, None, start, end).values_list("order__client_id", flat=True)
    )
    return [
        create_draft(actor, space, client, start, end)
        for client in Client.objects.filter(pk__in=list(client_ids)).order_by("name")
    ]


def _require_draft(invoice: Invoice) -> None:
    if invoice.status != Status.DRAFT:
        raise InvoicingError(_("Only drafts can be changed."))


def add_line(
    invoice: Invoice, description: str, quantity: Decimal, unit_price: Decimal, unit: str = ""
) -> InvoiceLine:
    _require_draft(invoice)
    if quantity == 0:
        raise InvoicingError(_("Quantity cannot be zero."))
    position = (
        invoice.lines.order_by("-position").values_list("position", flat=True).first() or 0
    ) + 1
    return InvoiceLine.objects.create(
        invoice=invoice,
        position=position,
        description=description,
        quantity=quantity,
        unit=unit,
        unit_price=unit_price,
        amount=_money(quantity * unit_price),
    )


def remove_line(line: InvoiceLine) -> None:
    _require_draft(line.invoice)
    line.delete()


def delete_draft(invoice: Invoice) -> None:
    _require_draft(invoice)
    invoice.delete()


@dataclass(frozen=True)
class Totals:
    excl_tax: Decimal
    tax: Decimal
    incl_tax: Decimal


def compute_totals(amounts: list[Decimal], vat_rate: Decimal) -> Totals:
    """VAT is computed once on the total excluding tax, rounded half up."""
    excl = sum(amounts, Decimal("0.00"))
    tax = _money(excl * vat_rate / Decimal(100))
    return Totals(_money(excl), tax, _money(excl + tax))


def vat_rate_for(invoice: Invoice, settings_: InvoiceSettings) -> Decimal:
    return invoice.client.vat_rate if invoice.client.vat_rate is not None else settings_.vat_rate


def _next_number(space: Space, kind: str, year: int, prefix: str) -> str:
    # The counter row is locked (FOR UPDATE on PostgreSQL; SQLite already holds
    # the write lock), so two concurrent issues never get the same number, and
    # a rolled-back issue gives its number back: no gaps.
    counter, _created = NumberCounter.objects.select_for_update().get_or_create(
        space=space, kind=kind, year=year
    )
    counter.last += 1
    counter.save(update_fields=["last"])
    return f"{prefix}{year}-{counter.last:04d}"


@transaction.atomic
def issue(
    actor: User,
    invoice: Invoice,
    issue_date: dt.date | None = None,
    *,
    vat_rate: Decimal | None = None,
) -> Invoice:
    invoice = (
        Invoice.objects.select_for_update().select_related("client", "space").get(pk=invoice.pk)
    )
    _require_draft(invoice)
    amounts = list(invoice.lines.values_list("amount", flat=True))
    if not amounts:
        raise InvoicingError(_("An invoice needs at least one line."))
    space = invoice.space
    conf = InvoiceSettings.for_space(space)
    issue_date = issue_date or timezone.localdate(timezone=zoneinfo.ZoneInfo(space.timezone))
    prefix = {
        Kind.CREDIT: conf.credit_prefix,
        Kind.QUOTE: conf.quote_prefix,
    }.get(Kind(invoice.kind), conf.number_prefix)
    invoice.number = _next_number(space, invoice.kind, issue_date.year, prefix)
    invoice.vat_rate = vat_rate if vat_rate is not None else vat_rate_for(invoice, conf)
    totals = compute_totals(amounts, invoice.vat_rate)
    invoice.total_excl_tax, invoice.total_tax, invoice.total_incl_tax = (
        totals.excl_tax,
        totals.tax,
        totals.incl_tax,
    )
    client = invoice.client
    invoice.client_name = client.name
    invoice.client_address = client.invoice_address
    invoice.client_reference = client.reference
    invoice.currency = space.currency
    invoice.issue_date = issue_date
    # For a quote, the "due date" is the end of its validity.
    days = conf.quote_validity_days if invoice.kind == Kind.QUOTE else conf.payment_terms_days
    invoice.due_date = issue_date + dt.timedelta(days=days)
    invoice.status = Status.ISSUED
    invoice.issued_by = actor
    invoice.save()

    from apps.invoices.pdf import render_invoice

    invoice.pdf.save(
        f"{invoice.number}.pdf", ContentFile(render_invoice(invoice, conf)), save=False
    )
    invoice.save(update_fields=["pdf", "updated_at"])
    return invoice


@transaction.atomic
def credit(actor: User, invoice: Invoice, reason: str = "") -> Invoice:
    """Cancel an issued invoice with a credit note mirroring all its lines."""
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != Status.ISSUED or invoice.kind != Kind.INVOICE:
        raise InvoicingError(_("Only issued invoices can be credited."))
    if Invoice.objects.filter(credited_invoice=invoice).exists():
        raise InvoicingError(_("This invoice has already been credited."))
    note = Invoice.objects.create(
        space=invoice.space,
        kind=Kind.CREDIT,
        client=invoice.client,
        period_start=invoice.period_start,
        period_end=invoice.period_end,
        credited_invoice=invoice,
        note=reason or _("Cancels invoice %(number)s") % {"number": invoice.number},
        currency=invoice.currency,
        created_by=actor,
    )
    InvoiceLine.objects.bulk_create(
        [
            InvoiceLine(
                invoice=note,
                position=line.position,
                description=line.description,
                quantity=-line.quantity,
                unit=line.unit,
                unit_price=line.unit_price,
                amount=-line.amount,
                booking=None,
            )
            for line in invoice.lines.all()
        ]
    )
    # Same VAT as the cancelled invoice, whatever the client's rate is now.
    return issue(actor, note, vat_rate=invoice.vat_rate)


def create_quote(actor: User, space: Space, client: Client, note: str = "") -> Invoice:
    """Empty draft quote; lines are added by hand."""
    if client.space_id != space.pk:
        raise InvoicingError(_("This client belongs to another platform."))
    today = timezone.localdate(timezone=zoneinfo.ZoneInfo(space.timezone))
    return Invoice.objects.create(
        space=space,
        kind=Kind.QUOTE,
        client=client,
        period_start=today,
        period_end=today,
        note=note,
        currency=space.currency,
        created_by=actor,
    )


def answer_quote(quote: Invoice, status: str) -> Invoice:
    if quote.kind != Kind.QUOTE or quote.status != Status.ISSUED:
        raise InvoicingError(_("Only sent quotes can be answered."))
    if status not in QuoteStatus.values:
        raise InvoicingError(_("Unknown status."))
    quote.quote_status = status
    quote.save(update_fields=["quote_status", "updated_at"])
    return quote


@transaction.atomic
def convert_quote(actor: User, quote: Invoice) -> Invoice:
    """Turn an accepted quote into a draft invoice with the same lines."""
    quote = Invoice.objects.select_for_update().get(pk=quote.pk)
    if quote.kind != Kind.QUOTE or quote.quote_status != QuoteStatus.ACCEPTED:
        raise InvoicingError(_("Only accepted quotes can be invoiced."))
    if quote.converted_to_id:
        raise InvoicingError(_("This quote has already been invoiced."))
    today = timezone.localdate(timezone=zoneinfo.ZoneInfo(quote.space.timezone))
    draft = Invoice.objects.create(
        space=quote.space,
        client=quote.client,
        period_start=today,
        period_end=today,
        note=_("According to quote %(number)s") % {"number": quote.number},
        currency=quote.currency,
        created_by=actor,
    )
    InvoiceLine.objects.bulk_create(
        [
            InvoiceLine(
                invoice=draft,
                position=line.position,
                description=line.description,
                quantity=line.quantity,
                unit=line.unit,
                unit_price=line.unit_price,
                amount=line.amount,
            )
            for line in quote.lines.all()
        ]
    )
    quote.converted_to = draft
    quote.save(update_fields=["converted_to", "updated_at"])
    return draft


def mark_paid(invoice: Invoice, on: dt.date | None) -> Invoice:
    if invoice.kind != Kind.INVOICE:
        raise InvoicingError(_("Only invoices can be paid."))
    if invoice.status != Status.ISSUED:
        raise InvoicingError(_("Only issued invoices can be paid."))
    invoice.paid_at = on
    invoice.save(update_fields=["paid_at", "updated_at"])
    return invoice


def space_invoices(space: Space, quotes: bool = False) -> QuerySet[Invoice]:
    """Invoices and credit notes of a space, or its quotes when ``quotes``."""
    qs = Invoice.objects.for_space(space).select_related("client", "credited_invoice")
    return qs.filter(kind=Kind.QUOTE) if quotes else qs.exclude(kind=Kind.QUOTE)
