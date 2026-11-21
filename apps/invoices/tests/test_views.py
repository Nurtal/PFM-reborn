import datetime as dt
import zoneinfo
from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.booking import services as booking
from apps.clients.tests.factories import ClientFactory, PriceFactory
from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory
from apps.invoices import services
from apps.invoices.models import Invoice, Status
from apps.resources.models import ALL_WEEKDAYS
from apps.resources.tests.factories import ResourceFactory

pytestmark = pytest.mark.django_db
PARIS = zoneinfo.ZoneInfo("Europe/Paris")


@pytest.fixture
def env(setup_done):
    resource = ResourceFactory()
    resource.area.open_weekdays = ALL_WEEKDAYS
    resource.area.save()
    client = ClientFactory(space=resource.space, name='=HYPERLINK("evil")')
    PriceFactory(resource=resource, grid=client.pricing_grid, day_rate=D("30"))
    manager = MembershipFactory(space=resource.space, role=Role.MANAGER).user
    start = dt.datetime(2026, 11, 3, 9, tzinfo=PARIS)
    booking.reserve(
        manager,
        resource,
        start,
        start + dt.timedelta(hours=2),
        client=client,
        now=start - dt.timedelta(days=30),
    )
    return resource.space, client, manager


def url(space, name, *args):
    return reverse(f"invoices:{name}", args=[space.slug, *args])


def test_full_flow_run_issue_pdf_credit(client, env):
    space, _customer, manager = env
    client.force_login(manager)
    response = client.post(
        url(space, "run"), {"period_start": "2026-11-01", "period_end": "2026-11-30"}
    )
    draft = Invoice.objects.get()
    assert response["Location"] == url(space, "detail", draft.public_id)
    assert client.get(url(space, "detail", draft.public_id)).status_code == 200

    client.post(url(space, "detail", draft.public_id), {"action": "issue"})
    draft.refresh_from_db()
    assert draft.status == Status.ISSUED and draft.number.startswith("F")
    pdf = client.get(url(space, "pdf", draft.public_id))
    assert pdf["Content-Type"] == "application/pdf"
    assert b"".join(pdf.streaming_content).startswith(b"%PDF")

    response = client.post(
        url(space, "detail", draft.public_id), {"action": "credit", "reason": "Erreur"}
    )
    note = Invoice.objects.get(credited_invoice=draft)
    assert response["Location"] == url(space, "detail", note.public_id)


def test_users_and_other_spaces_cannot_see_invoices(client, env):
    space, customer, manager = env
    draft = services.create_draft(
        manager, space, customer, dt.date(2026, 11, 1), dt.date(2026, 11, 30)
    ).invoice
    invoice = services.issue(manager, draft)
    plain_user = MembershipFactory(space=space, role=Role.USER).user
    client.force_login(plain_user)
    for name, args in [("list", []), ("pdf", [invoice.public_id]), ("detail", [invoice.public_id])]:
        assert client.get(url(space, name, *args)).status_code == 404
    other_manager = MembershipFactory(role=Role.MANAGER).user
    client.force_login(other_manager)
    other_space = other_manager.memberships.get().space
    # Right URL prefix for their own space, foreign invoice id: still 404.
    assert (
        client.get(reverse("invoices:pdf", args=[other_space.slug, invoice.public_id])).status_code
        == 404
    )


def test_csv_export_neutralises_formulas(client, env):
    space, customer, manager = env
    draft = services.create_draft(
        manager, space, customer, dt.date(2026, 11, 1), dt.date(2026, 11, 30)
    ).invoice
    services.issue(manager, draft)
    client.force_login(manager)
    body = client.get(url(space, "export", "csv")).content.decode()
    assert "'=HYPERLINK" in body
    assert "\n=HYPERLINK" not in body and ";=HYPERLINK" not in body


def test_xlsx_export(client, env):
    space, customer, manager = env
    services.issue(
        manager,
        services.create_draft(
            manager, space, customer, dt.date(2026, 11, 1), dt.date(2026, 11, 30)
        ).invoice,
    )
    client.force_login(manager)
    response = client.get(url(space, "export", "xlsx"))
    assert response.content[:2] == b"PK"


def test_issued_invoice_rejects_line_changes_through_ui(client, env):
    space, customer, manager = env
    invoice = services.issue(
        manager,
        services.create_draft(
            manager, space, customer, dt.date(2026, 11, 1), dt.date(2026, 11, 30)
        ).invoice,
    )
    client.force_login(manager)
    client.post(
        url(space, "detail", invoice.public_id),
        {"action": "add_line", "description": "x", "quantity": "1", "unit_price": "1"},
    )
    client.post(
        url(space, "detail", invoice.public_id),
        {"action": "remove_line", "line": invoice.lines.first().pk},
    )
    assert invoice.lines.count() == 1
