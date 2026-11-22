import datetime as dt
import zoneinfo
from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.booking import services as booking
from apps.clients.tests.factories import ClientFactory, PriceFactory
from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory
from apps.invoices import services as invoicing
from apps.resources.models import ALL_WEEKDAYS
from apps.resources.tests.factories import ResourceFactory
from apps.statistics import services

pytestmark = pytest.mark.django_db
PARIS = zoneinfo.ZoneInfo("Europe/Paris")


@pytest.fixture
def env():
    resource = ResourceFactory()
    area = resource.area
    area.open_weekdays, area.day_start, area.day_end = ALL_WEEKDAYS, dt.time(0), dt.time(0)
    area.save()
    client = ClientFactory(space=resource.space, name="Lab A")
    PriceFactory(resource=resource, grid=client.pricing_grid, day_rate=D("10"))
    manager = MembershipFactory(space=resource.space, role=Role.MANAGER).user
    return resource, client, manager


def book(resource, client, manager, start, hours):
    return booking.reserve(
        manager,
        resource,
        start,
        start + dt.timedelta(hours=hours),
        client=client,
        now=start - dt.timedelta(days=90),
    )


def test_hours_are_clipped_to_the_period(env):
    resource, client, manager = env
    # 22:00 on Oct 31 to 02:00 on Nov 1: only 2 hours fall in November.
    book(resource, client, manager, dt.datetime(2026, 10, 31, 22, tzinfo=PARIS), 4)
    book(resource, client, manager, dt.datetime(2026, 11, 10, 9, tzinfo=PARIS), 3)
    usage = services.resource_usage(resource.space, dt.date(2026, 11, 1), dt.date(2026, 11, 30))
    row = next(u for u in usage if u.resource == resource)
    assert row.bookings == 2
    assert row.hours == D("5.00")
    assert row.open_hours == D("720.00")  # 30 days around the clock
    assert row.occupancy == D("0.7")


def test_open_hours_follow_dst(env):
    resource, client, manager = env
    # 25 October 2026 lasts 25 hours in Paris.
    assert services.opening_time(
        resource, dt.date(2026, 10, 25), dt.date(2026, 10, 25)
    ) == dt.timedelta(hours=25)


def test_client_usage_and_revenue(env):
    resource, client, manager = env
    book(resource, client, manager, dt.datetime(2026, 11, 10, 9, tzinfo=PARIS), 3)
    clients = services.client_usage(resource.space, dt.date(2026, 11, 1), dt.date(2026, 11, 30))
    assert [(c.name, c.hours) for c in clients] == [("Lab A", D("3.00"))]
    draft = invoicing.create_draft(
        manager, resource.space, client, dt.date(2026, 11, 1), dt.date(2026, 11, 30)
    ).invoice
    invoice = invoicing.issue(manager, draft, dt.date(2026, 12, 1))
    invoicing.credit(manager, invoice)  # dated today: may fall in another month
    revenue = services.monthly_revenue(resource.space, dt.date(2026, 1, 1), dt.date(2027, 12, 31))
    assert sum(total for _month, total in revenue) == D("0")


def test_revenue_counts_issued_invoices(env):
    resource, client, manager = env
    book(resource, client, manager, dt.datetime(2026, 11, 10, 9, tzinfo=PARIS), 3)
    draft = invoicing.create_draft(
        manager, resource.space, client, dt.date(2026, 11, 1), dt.date(2026, 11, 30)
    ).invoice
    invoicing.issue(manager, draft, dt.date(2026, 12, 1))
    revenue = dict(
        services.monthly_revenue(resource.space, dt.date(2026, 12, 1), dt.date(2026, 12, 31))
    )
    assert revenue[dt.date(2026, 12, 1)] == D("30.00")


def test_dashboard_and_exports(client, env, setup_done):
    resource, customer, manager = env
    client.force_login(manager)
    space = resource.space
    params = {"start": "2026-11-01", "end": "2026-11-30"}
    response = client.get(reverse("statistics:dashboard", args=[space.slug]), params)
    assert response.status_code == 200
    assert b'id="charts-data"' in response.content
    for table in ("resources", "clients", "revenue"):
        csv = client.get(reverse("statistics:export", args=[space.slug, table]), params)
        assert csv.status_code == 200 and csv["Content-Type"].startswith("text/csv")


def test_dashboard_is_for_managers_and_rejects_huge_periods(client, env, setup_done):
    resource, customer, manager = env
    space = resource.space
    client.force_login(MembershipFactory(space=space, role=Role.USER).user)
    assert client.get(reverse("statistics:dashboard", args=[space.slug])).status_code == 404
    client.force_login(manager)
    response = client.get(
        reverse("statistics:dashboard", args=[space.slug]),
        {"start": "2000-01-01", "end": "2026-01-01"},
    )
    assert response.context["form"].errors


def test_csv_exports_neutralise_formulas(client, env, setup_done):
    resource, customer, manager = env
    resource.name = "=HYPERLINK(1)"
    resource.save()
    customer.name = "@SUM(A1)"
    customer.save()
    book(resource, customer, manager, dt.datetime(2026, 11, 10, 9, tzinfo=PARIS), 1)
    client.force_login(manager)
    params = {"start": "2026-11-01", "end": "2026-11-30"}
    space = resource.space.slug
    assert (
        "'=HYPERLINK"
        in client.get(
            reverse("statistics:export", args=[space, "resources"]), params
        ).content.decode()
    )
    assert (
        "'@SUM"
        in client.get(
            reverse("statistics:export", args=[space, "clients"]), params
        ).content.decode()
    )
