"""Glue between the database and the pure pricing functions."""

from __future__ import annotations

import datetime as dt
import zoneinfo
from decimal import Decimal

from django.db.models import QuerySet

from apps.accounts.models import User
from apps.clients import pricing
from apps.clients.models import Client, Package, PackagePrice, PricingGrid, ResourcePrice
from apps.core.models import Role, Space, user_role
from apps.resources.models import Resource


def grid_rules(grid: PricingGrid, space: Space) -> pricing.GridRules:
    return pricing.GridRules(
        tz=zoneinfo.ZoneInfo(space.timezone),
        night_enabled=grid.night_rate_enabled,
        night_start=grid.night_start,
        night_end=grid.night_end,
        weekend_enabled=grid.weekend_rate_enabled,
        weekend_days=grid.weekend_days,
    )


def clients_for(user: User, space: Space) -> QuerySet[Client]:
    """Clients ``user`` may book for: their own, or every client for managers."""
    qs = Client.objects.filter(space=space, is_active=True).select_related("pricing_grid")
    role = user_role(user, space)
    if user.is_superuser or (role is not None and role >= Role.MANAGER):
        return qs
    return qs.filter(users=user)


def booking_quote(
    resource: Resource,
    client: Client | None,
    start: dt.datetime,
    end: dt.datetime,
    package: Package | None = None,
) -> pricing.Quote | None:
    """Estimated price, or ``None`` when no price is defined for this client."""
    if client is None:
        return None
    grid = client.pricing_grid
    price = ResourcePrice.objects.filter(resource=resource, grid=grid).first()
    terms = None
    if package is not None:
        package_price = PackagePrice.objects.filter(package=package, grid=grid).first()
        if package_price is not None:
            terms = pricing.PackageTerms(
                package.name, dt.timedelta(minutes=package.duration_minutes), package_price.price
            )
    if price is not None:
        rates = pricing.Rates(price.day_rate, price.night_rate, price.weekend_rate)
    elif terms is not None and end - start <= terms.duration:
        rates = pricing.Rates(day=Decimal(0))  # never used: the package covers it all
    else:
        return None  # no hourly price to bill this (or the overage) with
    return pricing.quote(rates, grid_rules(grid, resource.space), start, end, package=terms)
