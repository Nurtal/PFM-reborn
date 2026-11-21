"""Usage and billing statistics, computed from bookings and issued invoices."""

from __future__ import annotations

import datetime as dt
import zoneinfo
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncMonth

from apps.booking.models import Booking, Status
from apps.booking.services import SlotRules
from apps.core.models import Space
from apps.invoices.models import Invoice
from apps.invoices.models import Status as InvoiceStatus
from apps.resources.models import Resource

HOUR = dt.timedelta(hours=1)


def period_bounds(space: Space, start: dt.date, end: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """[start 00:00, day after end 00:00) in the space time zone."""
    tz = zoneinfo.ZoneInfo(space.timezone)
    return (
        dt.datetime.combine(start, dt.time(0), tzinfo=tz),
        dt.datetime.combine(end + dt.timedelta(days=1), dt.time(0), tzinfo=tz),
    )


def clipped(start: dt.datetime, end: dt.datetime, lo: dt.datetime, hi: dt.datetime) -> dt.timedelta:
    """Length of [start, end) ∩ [lo, hi), in real time (UTC arithmetic)."""
    a, b = max(start, lo).astimezone(dt.UTC), min(end, hi).astimezone(dt.UTC)
    return max(b - a, dt.timedelta(0))


def opening_time(resource: Resource, start: dt.date, end: dt.date) -> dt.timedelta:
    """Total opening hours of the resource's area over the period (DST-aware)."""
    rules = SlotRules.for_resource(resource)
    total = dt.timedelta(0)
    day = start
    while day <= end:
        if rules.is_open_on(day):
            opens, closes = rules.opening_window(day)
            total += closes.astimezone(dt.UTC) - opens.astimezone(dt.UTC)
        day += dt.timedelta(days=1)
    return total


@dataclass(frozen=True)
class ResourceUsage:
    resource: Resource
    bookings: int
    hours: Decimal
    open_hours: Decimal

    @property
    def occupancy(self) -> Decimal | None:
        if not self.open_hours:
            return None
        return (self.hours * 100 / self.open_hours).quantize(Decimal("0.1"))


def _hours(delta: dt.timedelta) -> Decimal:
    return (Decimal(delta.total_seconds()) / Decimal(3600)).quantize(Decimal("0.01"))


def resource_usage(space: Space, start: dt.date, end: dt.date) -> list[ResourceUsage]:
    lo, hi = period_bounds(space, start, end)
    durations: dict[int, dt.timedelta] = defaultdict(dt.timedelta)
    counts: dict[int, int] = defaultdict(int)
    for b in Booking.objects.filter(
        space=space, status=Status.CONFIRMED, start__lt=hi, end__gt=lo
    ).only("resource_id", "start", "end"):
        durations[b.resource_id] += clipped(b.start, b.end, lo, hi)
        counts[b.resource_id] += 1
    resources = Resource.objects.for_space(space).select_related("area", "space")
    return [
        ResourceUsage(
            resource=r,
            bookings=counts.get(r.pk, 0),
            hours=_hours(durations.get(r.pk, dt.timedelta(0))),
            open_hours=_hours(opening_time(r, start, end)),
        )
        for r in resources
    ]


@dataclass(frozen=True)
class ClientUsage:
    name: str
    bookings: int
    hours: Decimal


def client_usage(space: Space, start: dt.date, end: dt.date) -> list[ClientUsage]:
    lo, hi = period_bounds(space, start, end)
    durations: dict[str, dt.timedelta] = defaultdict(dt.timedelta)
    counts: dict[str, int] = defaultdict(int)
    for b in Booking.objects.filter(
        space=space, status=Status.CONFIRMED, start__lt=hi, end__gt=lo
    ).select_related("client"):
        name = b.client.name if b.client else "—"
        durations[name] += clipped(b.start, b.end, lo, hi)
        counts[name] += 1
    rows = [ClientUsage(n, counts[n], _hours(d)) for n, d in durations.items()]
    return sorted(rows, key=lambda r: r.hours, reverse=True)


def monthly_revenue(space: Space, start: dt.date, end: dt.date) -> list[tuple[dt.date, Decimal]]:
    """Issued invoices minus credit notes, excluding tax, per month of issue."""
    rows = (
        Invoice.objects.filter(
            space=space,
            status=InvoiceStatus.ISSUED,
            issue_date__gte=start,
            issue_date__lte=end,
        )
        .annotate(month=TruncMonth("issue_date"))
        .values("month")
        .annotate(total=Sum("total_excl_tax"))
        .order_by("month")
    )
    by_month = {r["month"]: r["total"] or Decimal("0.00") for r in rows}
    months = []
    cursor = start.replace(day=1)
    while cursor <= end:
        months.append((cursor, by_month.get(cursor, Decimal("0.00"))))
        cursor = (cursor + dt.timedelta(days=32)).replace(day=1)
    return months
