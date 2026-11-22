"""The one and only price computation (README §6).

``price = f(rates of the resource for the client's grid, period (day / night /
weekend), duration, package)``. Pure functions, no database: the caller
gathers the inputs. Money is ``Decimal``, rounded per line to the cent
(half up), and durations are real elapsed time (DST-safe, computed in UTC).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

CENT = Decimal("0.01")
MIDNIGHT = dt.time(0)


class Period(StrEnum):
    DAY = "day"
    NIGHT = "night"
    WEEKEND = "weekend"
    PACKAGE = "package"


@dataclass(frozen=True)
class Rates:
    """Hourly rates; a missing night/weekend rate falls back to the day rate."""

    day: Decimal
    night: Decimal | None = None
    weekend: Decimal | None = None


@dataclass(frozen=True)
class GridRules:
    tz: dt.tzinfo
    night_enabled: bool = False
    night_start: dt.time = dt.time(19)
    night_end: dt.time = dt.time(8)
    weekend_enabled: bool = False
    weekend_days: int = 0b1100000  # Saturday, Sunday

    def is_weekend(self, day: dt.date) -> bool:
        return self.weekend_enabled and bool(self.weekend_days & (1 << day.weekday()))

    def is_night(self, t: dt.time) -> bool:
        if not self.night_enabled or self.night_start == self.night_end:
            return False
        if self.night_start > self.night_end:  # crosses midnight, e.g. 19:00-08:00
            return t >= self.night_start or t < self.night_end
        return self.night_start <= t < self.night_end


@dataclass(frozen=True)
class PackageTerms:
    name: str
    duration: dt.timedelta
    price: Decimal


@dataclass(frozen=True)
class QuoteLine:
    period: Period
    minutes: int
    unit_price: Decimal  # per hour, or the package price
    amount: Decimal


@dataclass(frozen=True)
class Quote:
    lines: tuple[QuoteLine, ...] = field(default_factory=tuple)

    @property
    def total(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal("0.00"))


def _boundaries(start: dt.datetime, end: dt.datetime, grid: GridRules) -> list[dt.datetime]:
    """Instants in (start, end) where the period can change, sorted, in UTC."""
    points = set()
    day = start.astimezone(grid.tz).date()
    last = end.astimezone(grid.tz).date()
    while day <= last + dt.timedelta(days=1):
        for t in (MIDNIGHT, grid.night_start, grid.night_end):
            instant = dt.datetime.combine(day, t, tzinfo=grid.tz).astimezone(dt.UTC)
            if start < instant < end:
                points.add(instant)
        day += dt.timedelta(days=1)
    return sorted(points)


def _segments(
    start: dt.datetime, end: dt.datetime, grid: GridRules
) -> Iterator[tuple[dt.datetime, dt.datetime]]:
    cursor = start
    for point in _boundaries(start, end, grid):
        yield cursor, point
        cursor = point
    yield cursor, end


def classify(moment: dt.datetime, grid: GridRules, rates: Rates) -> Period:
    """Weekend wins over night; a period without its own rate is billed as day."""
    local = moment.astimezone(grid.tz)
    if rates.weekend is not None and grid.is_weekend(local.date()):
        return Period.WEEKEND
    if rates.night is not None and grid.is_night(local.time()):
        return Period.NIGHT
    return Period.DAY


def _rate(period: Period, rates: Rates) -> Decimal:
    if period == Period.NIGHT and rates.night is not None:
        return rates.night
    if period == Period.WEEKEND and rates.weekend is not None:
        return rates.weekend
    return rates.day


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _hourly_lines(
    start: dt.datetime, end: dt.datetime, grid: GridRules, rates: Rates
) -> list[QuoteLine]:
    seconds: dict[Period, int] = {}
    for seg_start, seg_end in _segments(start, end, grid):
        period = classify(seg_start, grid, rates)
        seconds[period] = seconds.get(period, 0) + int((seg_end - seg_start).total_seconds())
    lines = []
    for period in (Period.DAY, Period.NIGHT, Period.WEEKEND):
        if period not in seconds:
            continue
        rate = _rate(period, rates)
        amount = _money(rate * Decimal(seconds[period]) / Decimal(3600))
        lines.append(QuoteLine(period, seconds[period] // 60, rate, amount))
    return lines


def quote(
    rates: Rates,
    grid: GridRules,
    start: dt.datetime,
    end: dt.datetime,
    package: PackageTerms | None = None,
) -> Quote:
    """Price of using a resource over ``[start, end)``.

    With a package, its fixed price covers the first ``package.duration``;
    any time beyond is billed hourly.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    start, end = start.astimezone(dt.UTC), end.astimezone(dt.UTC)
    if end <= start:
        return Quote()
    lines: list[QuoteLine] = []
    if package is not None:
        covered = min(end - start, package.duration)
        lines.append(
            QuoteLine(
                Period.PACKAGE,
                int(covered.total_seconds() // 60),
                package.price,
                _money(package.price),
            )
        )
        start = start + package.duration
        if start >= end:
            return Quote(tuple(lines))
    lines += _hourly_lines(start, end, grid, rates)
    return Quote(tuple(lines))
