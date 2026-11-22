"""Server-side Gantt layout: pure geometry, rendered as SVG by the template."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol


class Dated(Protocol):
    start_date: dt.date
    end_date: dt.date


@dataclass(frozen=True)
class Bar:
    item: object
    x: float  # percent of the window width
    width: float
    clipped_left: bool
    clipped_right: bool


@dataclass(frozen=True)
class Tick:
    x: float
    label: dt.date


@dataclass(frozen=True)
class Layout:
    start: dt.date
    end: dt.date  # exclusive
    bars: list[Bar]
    months: list[Tick]
    today: float | None


def _add_months(day: dt.date, months: int) -> dt.date:
    month = day.month - 1 + months
    return dt.date(day.year + month // 12, month % 12 + 1, 1)


def layout(items: Iterable[Dated], start: dt.date, months: int, today: dt.date) -> Layout:
    """Place items over ``months`` months from the first day of ``start``'s month.

    Items outside the window are dropped; items crossing an edge are clipped
    and flagged so the template can draw an arrow.
    """
    if months < 1:
        raise ValueError("months must be >= 1")
    first = start.replace(day=1)
    end = _add_months(first, months)
    span = (end - first).days

    def pos(day: dt.date) -> float:
        return round((day - first).days * 100 / span, 3)

    bars = []
    for item in items:
        last_day = item.end_date + dt.timedelta(days=1)  # end date is inclusive
        if last_day <= first or item.start_date >= end:
            continue
        lo, hi = max(item.start_date, first), min(last_day, end)
        bars.append(
            Bar(
                item=item,
                x=pos(lo),
                width=max(pos(hi) - pos(lo), 0.5),
                clipped_left=item.start_date < first,
                clipped_right=last_day > end,
            )
        )
    ticks = [Tick(pos(_add_months(first, m)), _add_months(first, m)) for m in range(months)]
    return Layout(first, end, bars, ticks, pos(today) if first <= today < end else None)
