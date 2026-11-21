"""Order business rules."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.clients.models import Client
from apps.clients.pricing import service_amount
from apps.services.models import (
    Order,
    OrderLine,
    OrderStatus,
    Project,
    ServiceItem,
    ServicePrice,
    Shelf,
    StockEntry,
)


class OrderError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def unit_price(item: ServiceItem, client: Client) -> Decimal | None:
    price = ServicePrice.objects.filter(item=item, grid_id=client.pricing_grid_id).first()
    return price.price if price is not None else None


def line_amount(line: OrderLine) -> Decimal | None:
    price = unit_price(line.item, line.order.client)
    return None if price is None else service_amount(price, line.quantity)


def _require_open(order: Order) -> None:
    if order.status != OrderStatus.OPEN:
        raise OrderError(_("This order is closed."))


def add_line(
    order: Order,
    item: ServiceItem,
    quantity: Decimal,
    performed_on: dt.date | None = None,
    comment: str = "",
) -> OrderLine:
    _require_open(order)
    if item.space_id != order.space_id or not item.is_active:
        raise OrderError(_("This service is not available on this platform."))
    if quantity <= 0:
        raise OrderError(_("The quantity must be positive."))
    return OrderLine.objects.create(
        order=order,
        item=item,
        quantity=quantity,
        performed_on=performed_on or timezone.localdate(),
        comment=comment,
    )


def remove_line(line: OrderLine) -> None:
    _require_open(line.order)
    if line.invoice_lines.filter(
        invoice__credit_note__isnull=True, invoice__kind="invoice"
    ).exists():
        raise OrderError(_("This line is already invoiced."))
    line.delete()


def set_status(order: Order, status: str) -> Order:
    if status not in OrderStatus.values:
        raise OrderError(_("Unknown status."))
    if (
        status == OrderStatus.CANCELLED
        and order.lines.filter(
            invoice_lines__invoice__credit_note__isnull=True, invoice_lines__invoice__kind="invoice"
        ).exists()
    ):
        raise OrderError(_("An order with invoiced lines cannot be cancelled."))
    order.status = status
    order.closed_at = None if status == OrderStatus.OPEN else timezone.now()
    order.save(update_fields=["status", "closed_at"])
    return order


# --- Projects -------------------------------------------------------------------


def project_total(project: Project) -> tuple[Decimal, int]:
    """Amount of the project's priced order lines, and how many lines lack a price."""
    total, unpriced = Decimal("0.00"), 0
    for line in (
        OrderLine.objects.filter(order__project=project)
        .exclude(order__status=OrderStatus.CANCELLED)
        .select_related("item", "order__client")
    ):
        amount = line_amount(line)
        if amount is None:
            unpriced += 1
        else:
            total += amount
    return total, unpriced


# --- Stock ------------------------------------------------------------------------


def shelf_load(shelf: Shelf) -> int:
    return int(
        StockEntry.objects.filter(shelf=shelf, removed_on__isnull=True).aggregate(
            n=models.Sum("quantity")
        )["n"]
        or 0
    )


def store(
    shelf: Shelf, client: Client, label: str, quantity: int = 1, note: str = ""
) -> StockEntry:
    space_id = shelf.cabinet.space_id
    if client.space_id != space_id:
        raise OrderError(_("This client belongs to another platform."))
    if quantity < 1:
        raise OrderError(_("The quantity must be positive."))
    if shelf.capacity is not None and shelf_load(shelf) + quantity > shelf.capacity:
        raise OrderError(
            _("Not enough room on this shelf (%(free)d left).")
            % {"free": shelf.capacity - shelf_load(shelf)}
        )
    return StockEntry.objects.create(
        space_id=space_id, shelf=shelf, client=client, label=label, quantity=quantity, note=note
    )


def withdraw(entry: StockEntry, on: dt.date | None = None) -> StockEntry:
    if entry.removed_on is not None:
        raise OrderError(_("Already withdrawn."))
    on = on or timezone.localdate()
    if on < entry.stored_on:
        raise OrderError(_("Withdrawal cannot precede storage."))
    entry.removed_on = on
    entry.save(update_fields=["removed_on"])
    return entry
