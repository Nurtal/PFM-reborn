import datetime as dt
from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.clients.tests.factories import ClientFactory
from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory, SpaceFactory
from apps.invoices import services as invoicing
from apps.services import services
from apps.services.models import Order, OrderStatus, ServiceItem, ServicePrice

pytestmark = pytest.mark.django_db
NOV = (dt.date(2026, 11, 1), dt.date(2026, 11, 30))


@pytest.fixture
def env():
    space = SpaceFactory()
    client = ClientFactory(space=space)
    item = ServiceItem.objects.create(space=space, name="Sample prep", unit="sample")
    ServicePrice.objects.create(space=space, item=item, grid=client.pricing_grid, price=D("12.50"))
    manager = MembershipFactory(space=space, role=Role.MANAGER).user
    order = Order.objects.create(space=space, client=client, title="Project X")
    return space, client, item, manager, order


def test_order_lines_are_priced_by_client_grid(env):
    space, client, item, manager, order = env
    line = services.add_line(order, item, D("3"), dt.date(2026, 11, 4))
    assert services.line_amount(line) == D("37.50")


def test_order_lines_are_invoiced_once(env):
    space, client, item, manager, order = env
    services.add_line(order, item, D("4"), dt.date(2026, 11, 4))
    services.add_line(order, item, D("1"), dt.date(2026, 12, 4))  # next period
    draft = invoicing.create_draft(manager, space, client, *NOV).invoice
    assert [line.amount for line in draft.lines.all()] == [D("50.00")]
    assert invoicing.create_draft(manager, space, client, *NOV).invoice is None
    invoice = invoicing.issue(manager, draft)
    # Invoiced lines cannot be removed, and the order cannot be cancelled.
    with pytest.raises(services.OrderError):
        services.remove_line(order.lines.get(performed_on=dt.date(2026, 11, 4)))
    with pytest.raises(services.OrderError):
        services.set_status(order, OrderStatus.CANCELLED)
    assert invoice.total_excl_tax == D("50.00")


def test_cancelled_orders_are_not_billed(env):
    space, client, item, manager, order = env
    services.add_line(order, item, D("2"), dt.date(2026, 11, 4))
    services.set_status(order, OrderStatus.CANCELLED)
    assert invoicing.create_draft(manager, space, client, *NOV).invoice is None


def test_closed_orders_refuse_new_lines(env):
    space, client, item, manager, order = env
    services.set_status(order, OrderStatus.DONE)
    with pytest.raises(services.OrderError):
        services.add_line(order, item, D("1"))


def test_service_of_another_space_is_refused(env):
    space, client, item, manager, order = env
    foreign = ServiceItem.objects.create(space=SpaceFactory(), name="Foreign")
    with pytest.raises(services.OrderError):
        services.add_line(order, foreign, D("1"))


def test_unpriced_lines_are_reported(env):
    space, client, item, manager, order = env
    free = ServiceItem.objects.create(space=space, name="Consulting")
    services.add_line(order, free, D("1"), dt.date(2026, 11, 4))
    result = invoicing.create_draft(manager, space, client, *NOV)
    assert result.invoice is None and len(result.unpriced) == 1


def test_bookings_and_orders_on_the_same_invoice(env, monkeypatch):
    space, client, item, manager, order = env
    services.add_line(order, item, D("2"), dt.date(2026, 11, 4))
    results = invoicing.create_drafts_for_period(manager, space, *NOV)
    assert len(results) == 1 and results[0].invoice.lines.count() == 1


def test_order_pages_are_for_managers(client, env, setup_done):
    space, customer, item, manager, order = env
    user = MembershipFactory(space=space, role=Role.USER).user
    client.force_login(user)
    assert client.get(reverse("services:orders", args=[space.slug])).status_code == 404
    client.force_login(manager)
    detail = reverse("services:order_detail", args=[space.slug, order.public_id])
    response = client.post(
        detail,
        {"action": "add_line", "item": item.pk, "quantity": "2", "performed_on": "2026-11-05"},
    )
    assert response.status_code == 302
    assert order.lines.count() == 1
    assert client.get(detail).status_code == 200


def test_service_prices_in_matrix(client, env, setup_done):
    space, customer, item, manager, order = env
    client.force_login(manager)
    url = reverse("clients:prices", args=[space.slug])
    client.post(url, {f"service-{item.pk}-{customer.pricing_grid.pk}": "15"})
    assert ServicePrice.objects.get(item=item).price == D("15")
