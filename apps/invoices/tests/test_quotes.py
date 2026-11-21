import datetime as dt
from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.clients.tests.factories import ClientFactory
from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory, SpaceFactory
from apps.invoices import services
from apps.invoices.models import Invoice, Kind, QuoteStatus, Status
from apps.statistics.services import monthly_revenue

pytestmark = pytest.mark.django_db


@pytest.fixture
def env():
    space = SpaceFactory()
    client = ClientFactory(space=space)
    manager = MembershipFactory(space=space, role=Role.MANAGER).user
    return space, client, manager


def issued_quote(space, client, manager):
    quote = services.create_quote(manager, space, client)
    services.add_line(quote, "Imaging study", D("10"), D("45"), "h")
    return services.issue(manager, quote, dt.date(2026, 11, 2))


def test_quote_has_its_own_numbering_and_validity(env):
    space, client, manager = env
    quote = issued_quote(space, client, manager)
    assert quote.number == "D2026-0001"
    assert quote.due_date == dt.date(2026, 12, 2)
    assert quote.total_incl_tax == D("540.00")
    assert quote.pdf.read().startswith(b"%PDF")


def test_quotes_are_not_revenue_nor_payable(env):
    space, client, manager = env
    quote = issued_quote(space, client, manager)
    assert (
        sum(t for _m, t in monthly_revenue(space, dt.date(2026, 11, 1), dt.date(2026, 11, 30))) == 0
    )
    with pytest.raises(services.InvoicingError):
        services.mark_paid(quote, dt.date(2026, 11, 3))
    with pytest.raises(services.InvoicingError):
        services.credit(manager, quote)


def test_accept_then_convert_once(env):
    space, client, manager = env
    quote = issued_quote(space, client, manager)
    with pytest.raises(services.InvoicingError):
        services.convert_quote(manager, quote)  # still pending
    services.answer_quote(quote, QuoteStatus.ACCEPTED)
    draft = services.convert_quote(manager, quote)
    assert draft.kind == Kind.INVOICE and draft.status == Status.DRAFT
    assert [line.amount for line in draft.lines.all()] == [D("450.00")]
    assert services.issue(manager, draft, dt.date(2026, 11, 5)).number == "F2026-0001"
    with pytest.raises(services.InvoicingError):
        services.convert_quote(manager, Invoice.objects.get(pk=quote.pk))


def test_draft_quote_cannot_be_answered(env):
    space, client, manager = env
    quote = services.create_quote(manager, space, client)
    with pytest.raises(services.InvoicingError):
        services.answer_quote(quote, QuoteStatus.ACCEPTED)


def test_quote_ui_flow(client, env, setup_done):
    space, customer, manager = env
    client.force_login(manager)
    response = client.post(
        reverse("invoices:quote_create", args=[space.slug]), {"client": customer.pk}
    )
    quote = Invoice.objects.get(kind=Kind.QUOTE)
    detail = reverse("invoices:detail", args=[space.slug, quote.public_id])
    assert response["Location"] == detail
    client.post(
        detail, {"action": "add_line", "description": "Study", "quantity": "2", "unit_price": "100"}
    )
    client.post(detail, {"action": "issue"})
    client.post(detail, {"action": "answer", "quote_status": "accepted"})
    response = client.post(detail, {"action": "convert"})
    invoice = Invoice.objects.get(kind=Kind.INVOICE)
    assert response["Location"] == reverse("invoices:detail", args=[space.slug, invoice.public_id])
    listing = client.get(
        reverse("invoices:list", args=[space.slug]), {"status": "quotes"}
    ).content.decode()
    assert quote.public_id.hex[:8] in listing.replace("-", "")
    # Quotes never appear in the accounting export.
    export = client.get(reverse("invoices:export", args=[space.slug, "csv"])).content.decode()
    assert "D2026" not in export


def test_foreign_client_quote_refused(client, env, setup_done):
    space, customer, manager = env
    client.force_login(manager)
    response = client.post(
        reverse("invoices:quote_create", args=[space.slug]), {"client": ClientFactory().pk}
    )
    assert response.status_code == 404
