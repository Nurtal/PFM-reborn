import datetime as dt
from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.clients.tests.factories import ClientFactory
from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory, SpaceFactory
from apps.services import services
from apps.services.models import (
    Cabinet,
    Order,
    OrderStatus,
    Project,
    ServiceItem,
    ServicePrice,
    Shelf,
    StockEntry,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def env():
    space = SpaceFactory()
    client = ClientFactory(space=space)
    manager = MembershipFactory(space=space, role=Role.MANAGER).user
    return space, client, manager


def test_project_total_sums_priced_lines_of_live_orders(env):
    space, client, manager = env
    project = Project.objects.create(
        space=space,
        name="Atlas",
        client=client,
        start_date=dt.date(2026, 1, 1),
        end_date=dt.date(2026, 6, 30),
    )
    item = ServiceItem.objects.create(space=space, name="Prep")
    free = ServiceItem.objects.create(space=space, name="Advice")
    ServicePrice.objects.create(space=space, item=item, grid=client.pricing_grid, price=D("10"))
    live = Order.objects.create(space=space, client=client, project=project, title="A")
    dead = Order.objects.create(space=space, client=client, project=project, title="B")
    services.add_line(live, item, D("3"))
    services.add_line(live, free, D("1"))
    services.add_line(dead, item, D("100"))
    services.set_status(dead, OrderStatus.CANCELLED)
    assert services.project_total(project) == (D("30.00"), 1)


def test_projects_page_and_gantt(client, env, setup_done):
    space, customer, manager = env
    today = dt.date.today()
    Project.objects.create(
        space=space,
        name="Visible",
        client=customer,
        start_date=today,
        end_date=today + dt.timedelta(days=30),
    )
    Project.objects.create(
        space=space,
        name="Closed one",
        client=customer,
        start_date=today,
        end_date=today,
        status="closed",
    )
    client.force_login(manager)
    response = client.get(reverse("services:projects", args=[space.slug]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Visible" in body and "Closed one" not in body
    assert "<rect" in body
    assert (
        "Closed one"
        in client.get(
            reverse("services:projects", args=[space.slug]), {"closed": 1}
        ).content.decode()
    )


def test_project_form_rejects_reversed_dates(client, env, setup_done):
    space, customer, manager = env
    client.force_login(manager)
    response = client.post(
        reverse("services:project_create", args=[space.slug]),
        {
            "name": "X",
            "client": customer.pk,
            "start_date": "2026-05-01",
            "end_date": "2026-04-01",
            "status": "planned",
        },
    )
    assert response.status_code == 200
    assert not Project.objects.exists()


@pytest.fixture
def shelf(env):
    space, _client, _manager = env
    cabinet = Cabinet.objects.create(space=space, name="Freezer -80")
    return Shelf.objects.create(cabinet=cabinet, name="Rack 1", capacity=5)


def test_store_respects_capacity(env, shelf):
    space, customer, manager = env
    services.store(shelf, customer, "Mouse brains", 4)
    with pytest.raises(services.OrderError, match="room"):
        services.store(shelf, customer, "More", 2)
    entry = StockEntry.objects.get()
    services.withdraw(entry)
    services.store(shelf, customer, "More", 2)  # room again
    assert services.shelf_load(shelf) == 2


def test_store_refuses_foreign_client(env, shelf):
    with pytest.raises(services.OrderError):
        services.store(shelf, ClientFactory(), "x")


def test_withdraw_rules(env, shelf):
    space, customer, manager = env
    entry = services.store(shelf, customer, "Tubes")
    with pytest.raises(services.OrderError):
        services.withdraw(entry, entry.stored_on - dt.timedelta(days=1))
    services.withdraw(entry)
    with pytest.raises(services.OrderError):
        services.withdraw(entry)


def test_stock_page_flows_and_isolation(client, env, shelf, setup_done):
    space, customer, manager = env
    client.force_login(manager)
    url = reverse("services:stock", args=[space.slug])
    response = client.post(
        url,
        {
            "action": "store",
            "shelf": shelf.pk,
            "client": customer.pk,
            "label": "Slides",
            "quantity": 2,
        },
    )
    assert response.status_code == 302
    assert StockEntry.objects.get().label == "Slides"
    # A shelf of another platform is not a valid choice.
    other_shelf = Shelf.objects.create(
        cabinet=Cabinet.objects.create(space=SpaceFactory(), name="C"), name="S"
    )
    response = client.post(
        url,
        {
            "action": "store",
            "shelf": other_shelf.pk,
            "client": customer.pk,
            "label": "X",
            "quantity": 1,
        },
    )
    assert response.status_code == 200 and StockEntry.objects.count() == 1
    user = MembershipFactory(space=space, role=Role.USER).user
    client.force_login(user)
    assert client.get(url).status_code == 404
