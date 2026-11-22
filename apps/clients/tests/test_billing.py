import datetime as dt
import zoneinfo
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.booking import services
from apps.clients.models import PackagePrice, ResourcePrice
from apps.clients.services import booking_quote, clients_for
from apps.clients.tests.factories import ClientFactory, GridFactory, PackageFactory, PriceFactory
from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory
from apps.resources.models import ALL_WEEKDAYS
from apps.resources.tests.factories import ResourceFactory

pytestmark = pytest.mark.django_db
PARIS = zoneinfo.ZoneInfo("Europe/Paris")


@pytest.fixture
def resource():
    r = ResourceFactory()
    r.area.open_weekdays = ALL_WEEKDAYS
    r.area.save()
    return r


def slot(hours=2, day_offset=2):
    start = (
        (timezone.now() + dt.timedelta(days=day_offset))
        .astimezone(PARIS)
        .replace(hour=9, minute=0, second=0, microsecond=0)
    )
    return start, start + dt.timedelta(hours=hours)


def member(resource, role=Role.USER):
    return MembershipFactory(space=resource.space, role=role).user


def test_clients_for_user_and_manager(resource):
    mine, other = ClientFactory(space=resource.space), ClientFactory(space=resource.space)
    ClientFactory()  # other space
    user = member(resource)
    mine.users.add(user)
    assert list(clients_for(user, resource.space)) == [mine]
    assert set(clients_for(member(resource, Role.MANAGER), resource.space)) == {mine, other}


def test_user_with_clients_must_choose_one(resource):
    client = ClientFactory(space=resource.space)
    user = member(resource)
    client.users.add(user)
    with pytest.raises(services.BookingError, match="client"):
        services.reserve(user, resource, *slot())
    booking = services.reserve(user, resource, *slot(), client=client)
    assert booking.client == client


def test_user_without_clients_can_book_without_one(resource):
    services.reserve(member(resource), resource, *slot())


def test_user_cannot_bill_someone_elses_client(resource):
    theirs = ClientFactory(space=resource.space)
    user = member(resource)
    with pytest.raises(services.BookingError, match="cannot bill"):
        services.reserve(user, resource, *slot(), client=theirs)


def test_client_of_another_space_is_refused_even_for_managers(resource):
    foreign = ClientFactory()
    with pytest.raises(services.BookingError):
        services.reserve(member(resource, Role.MANAGER), resource, *slot(), client=foreign)


def test_package_must_belong_to_the_resource(resource):
    other = ResourceFactory(space=resource.space)
    package = PackageFactory(resource=other)
    with pytest.raises(services.BookingError, match="package"):
        services.reserve(member(resource), resource, *slot(), package=package)


def test_quote_uses_client_grid(resource):
    academic = ClientFactory(space=resource.space)
    industry = ClientFactory(space=resource.space)
    PriceFactory(resource=resource, grid=academic.pricing_grid, day_rate=Decimal("20"))
    PriceFactory(resource=resource, grid=industry.pricing_grid, day_rate=Decimal("80"))
    start, end = slot(hours=2)
    assert booking_quote(resource, academic, start, end).total == Decimal("40.00")
    assert booking_quote(resource, industry, start, end).total == Decimal("160.00")


def test_quote_none_without_price_or_client(resource):
    client = ClientFactory(space=resource.space)
    start, end = slot()
    assert booking_quote(resource, client, start, end) is None
    assert booking_quote(resource, None, start, end) is None


def test_package_quote_and_overage_without_hourly_price(resource):
    client = ClientFactory(space=resource.space)
    package = PackageFactory(resource=resource, duration_minutes=120)
    PackagePrice.objects.create(
        space=resource.space, package=package, grid=client.pricing_grid, price=Decimal("50")
    )
    assert booking_quote(resource, client, *slot(hours=2), package=package).total == Decimal(
        "50.00"
    )
    # Longer than the package and no hourly rate: cannot be priced.
    assert booking_quote(resource, client, *slot(hours=3), package=package) is None


def _manager_login(client_http, resource):
    manager = member(resource, Role.MANAGER)
    client_http.force_login(manager)
    return manager


def test_price_matrix_saves_and_ignores_foreign_ids(client, resource, setup_done):
    _manager_login(client, resource)
    grid = GridFactory(space=resource.space, night_rate_enabled=True)
    foreign_resource = ResourceFactory()
    url = reverse("clients:prices", args=[resource.space.slug])
    response = client.post(
        url,
        {
            f"rate-{resource.pk}-{grid.pk}-day": "35,50",
            f"rate-{resource.pk}-{grid.pk}-night": "50",
            f"rate-{foreign_resource.pk}-{grid.pk}-day": "1",
        },
    )
    assert response.status_code == 302
    price = ResourcePrice.objects.get()
    assert (price.resource, price.day_rate, price.night_rate) == (
        resource,
        Decimal("35.50"),
        Decimal("50"),
    )


def test_price_matrix_rejects_bad_amounts_atomically(client, resource, setup_done):
    _manager_login(client, resource)
    grid = GridFactory(space=resource.space)
    other = ResourceFactory(space=resource.space)
    response = client.post(
        reverse("clients:prices", args=[resource.space.slug]),
        {f"rate-{resource.pk}-{grid.pk}-day": "10", f"rate-{other.pk}-{grid.pk}-day": "-3"},
    )
    assert response.status_code == 200
    assert not ResourcePrice.objects.exists()


def test_clients_pages_are_for_managers(client, resource, setup_done):
    client.force_login(member(resource))
    for name in ("clients:list", "clients:prices", "clients:create"):
        assert client.get(reverse(name, args=[resource.space.slug])).status_code == 404


def test_quote_partial_in_dialog(client, resource, setup_done):
    user = member(resource)
    lab = ClientFactory(space=resource.space)
    lab.users.add(user)
    PriceFactory(resource=resource, grid=lab.pricing_grid, day_rate=Decimal("30"))
    client.force_login(user)
    start, end = slot(hours=2)
    fmt = "%Y-%m-%dT%H:%M"
    response = client.get(
        reverse("booking:quote", args=[resource.space.slug, resource.public_id]),
        {"start": start.strftime(fmt), "end": end.strftime(fmt), "client": lab.pk},
    )
    assert response.status_code == 200
    assert "60.00" in response.content.decode()
