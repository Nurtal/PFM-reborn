import datetime as dt
import threading
import zoneinfo
from decimal import Decimal as D

import pytest
from django.db import connection

from apps.booking import services as booking
from apps.clients.tests.factories import ClientFactory, PriceFactory
from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory
from apps.invoices import services
from apps.invoices.models import ImmutableInvoiceError, Invoice, InvoiceSettings, Kind, Status
from apps.resources.models import ALL_WEEKDAYS
from apps.resources.tests.factories import ResourceFactory

pytestmark = pytest.mark.django_db
PARIS = zoneinfo.ZoneInfo("Europe/Paris")
NOV = (dt.date(2026, 11, 1), dt.date(2026, 11, 30))


@pytest.fixture
def setup_billing():
    resource = ResourceFactory()
    resource.area.open_weekdays = ALL_WEEKDAYS
    resource.area.save()
    client = ClientFactory(space=resource.space, invoice_address="1 rue X\n75000 Paris")
    PriceFactory(resource=resource, grid=client.pricing_grid, day_rate=D("30.00"))
    manager = MembershipFactory(space=resource.space, role=Role.MANAGER).user
    return resource, client, manager


def book(resource, client, manager, day, hours=2, h=9):
    start = dt.datetime(2026, 11, day, h, tzinfo=PARIS)
    return booking.reserve(
        manager,
        resource,
        start,
        start + dt.timedelta(hours=hours),
        client=client,
        now=start - dt.timedelta(days=60),
    )


def test_draft_contains_period_bookings_priced(setup_billing):
    resource, client, manager = setup_billing
    book(resource, client, manager, 3)
    book(resource, client, manager, 4, hours=1)
    book(resource, client, manager, 2, hours=1)
    start = dt.datetime(2026, 12, 1, 9, tzinfo=PARIS)  # outside the period
    booking.reserve(
        manager,
        resource,
        start,
        start + dt.timedelta(hours=1),
        client=client,
        now=start - dt.timedelta(days=60),
    )

    result = services.create_draft(manager, resource.space, client, *NOV)
    lines = list(result.invoice.lines.all())
    assert [line.amount for line in lines] == [D("30.00"), D("60.00"), D("30.00")]
    assert lines[1].quantity == D("2.00") and lines[1].unit == "h"
    assert result.invoice.status == Status.DRAFT and result.invoice.number is None


def test_booking_is_never_billed_twice(setup_billing):
    resource, client, manager = setup_billing
    book(resource, client, manager, 3)
    assert services.create_draft(manager, resource.space, client, *NOV).invoice
    assert services.create_draft(manager, resource.space, client, *NOV).invoice is None


def test_cancelled_bookings_are_not_billed(setup_billing):
    resource, client, manager = setup_billing
    b = book(resource, client, manager, 3)
    booking.cancel(manager, b)
    assert services.create_draft(manager, resource.space, client, *NOV).invoice is None


def test_unpriced_bookings_are_reported_not_billed_at_zero(setup_billing):
    resource, _client, manager = setup_billing
    other = ClientFactory(space=resource.space)  # no price for its grid
    book(resource, other, manager, 3)
    result = services.create_draft(manager, resource.space, other, *NOV)
    assert result.invoice is None
    assert len(result.unpriced) == 1


def test_issue_numbers_freezes_and_totals(setup_billing):
    resource, client, manager = setup_billing
    book(resource, client, manager, 3, hours=3)  # 90.00
    draft = services.create_draft(manager, resource.space, client, *NOV).invoice
    invoice = services.issue(manager, draft, dt.date(2026, 12, 2))
    assert invoice.number == "F2026-0001"
    assert (invoice.total_excl_tax, invoice.total_tax, invoice.total_incl_tax) == (
        D("90.00"),
        D("18.00"),
        D("108.00"),
    )
    assert invoice.client_name == client.name
    assert invoice.due_date == dt.date(2027, 1, 1)
    assert invoice.pdf.read().startswith(b"%PDF")


def test_numbers_are_sequential_per_space_and_reset_each_year(setup_billing):
    resource, client, manager = setup_billing
    numbers = []
    for day, issued_on in [
        (3, dt.date(2026, 12, 1)),
        (4, dt.date(2026, 12, 2)),
        (5, dt.date(2027, 1, 3)),
    ]:
        book(resource, client, manager, day)
        draft = services.create_draft(manager, resource.space, client, *NOV).invoice
        numbers.append(services.issue(manager, draft, issued_on).number)
    assert numbers == ["F2026-0001", "F2026-0002", "F2027-0001"]
    # Another platform has its own sequence.
    r2 = ResourceFactory()
    c2 = ClientFactory(space=r2.space)
    PriceFactory(resource=r2, grid=c2.pricing_grid)
    m2 = MembershipFactory(space=r2.space, role=Role.MANAGER).user
    r2.area.open_weekdays = ALL_WEEKDAYS
    r2.area.save()
    book(r2, c2, m2, 3)
    other = services.issue(
        m2, services.create_draft(m2, r2.space, c2, *NOV).invoice, dt.date(2026, 12, 1)
    )
    assert other.number == "F2026-0001"


def test_drafts_do_not_consume_numbers(setup_billing):
    resource, client, manager = setup_billing
    book(resource, client, manager, 3)
    services.delete_draft(services.create_draft(manager, resource.space, client, *NOV).invoice)
    book(resource, client, manager, 4)
    draft = services.create_draft(manager, resource.space, client, *NOV).invoice
    assert services.issue(manager, draft, dt.date(2026, 12, 1)).number == "F2026-0001"


def test_issued_invoice_is_immutable(setup_billing):
    resource, client, manager = setup_billing
    book(resource, client, manager, 3)
    invoice = services.issue(
        manager, services.create_draft(manager, resource.space, client, *NOV).invoice
    )
    invoice.total_excl_tax = D("1.00")
    with pytest.raises(ImmutableInvoiceError):
        invoice.save()
    with pytest.raises(ImmutableInvoiceError):
        invoice.lines.first().delete()
    with pytest.raises(ImmutableInvoiceError):
        Invoice.objects.get(pk=invoice.pk).delete()
    with pytest.raises(services.InvoicingError):
        services.add_line(invoice, "x", D(1), D(1))
    services.mark_paid(Invoice.objects.get(pk=invoice.pk), dt.date(2026, 12, 20))  # allowed


def test_credit_note_cancels_and_frees_bookings(setup_billing):
    resource, client, manager = setup_billing
    book(resource, client, manager, 3)
    invoice = services.issue(
        manager,
        services.create_draft(manager, resource.space, client, *NOV).invoice,
        dt.date(2026, 12, 1),
    )
    client.vat_rate = D("0")  # rate changed since: the credit note keeps the original VAT
    client.save()
    note = services.credit(manager, invoice, "Wrong grid")
    assert note.kind == Kind.CREDIT and note.number == "A2026-0001"
    assert note.total_incl_tax == -invoice.total_incl_tax
    assert note.vat_rate == invoice.vat_rate
    with pytest.raises(services.InvoicingError):
        services.credit(manager, invoice)
    # The booking can be invoiced again (e.g. with the right grid).
    again = services.create_draft(manager, resource.space, client, *NOV).invoice
    assert again is not None
    assert services.issue(manager, again, dt.date(2026, 12, 3)).number == "F2026-0002"


def test_client_vat_override_and_settings(setup_billing):
    resource, client, manager = setup_billing
    conf = InvoiceSettings.for_space(resource.space)
    conf.vat_rate = D("5.50")
    conf.number_prefix = "IMG-"
    conf.save()
    book(resource, client, manager, 3)  # 60.00
    invoice = services.issue(
        manager,
        services.create_draft(manager, resource.space, client, *NOV).invoice,
        dt.date(2026, 12, 1),
    )
    assert invoice.number == "IMG-2026-0001"
    assert invoice.total_tax == D("3.30")


def test_vat_rounding_half_up():
    totals = services.compute_totals([D("10.05")], D("5.5"))  # 0.55275 -> 0.55
    assert totals.tax == D("0.55")
    totals = services.compute_totals([D("0.10")], D("5"))  # 0.005 -> 0.01
    assert totals.tax == D("0.01")


def test_manual_lines_on_draft(setup_billing):
    resource, client, manager = setup_billing
    book(resource, client, manager, 3)
    draft = services.create_draft(manager, resource.space, client, *NOV).invoice
    line = services.add_line(draft, "Training session", D("1.5"), D("100.333"))
    assert line.amount == D("150.50")
    services.remove_line(line)
    assert draft.lines.count() == 1


def test_monthly_run_creates_one_draft_per_client(setup_billing):
    resource, client, manager = setup_billing
    other = ClientFactory(space=resource.space, pricing_grid=client.pricing_grid)
    book(resource, client, manager, 3)
    book(resource, other, manager, 4)
    results = services.create_drafts_for_period(manager, resource.space, *NOV)
    assert sorted(r.invoice.client.name for r in results) == sorted([client.name, other.name])


@pytest.mark.django_db(transaction=True)
def test_concurrent_issues_get_distinct_gapless_numbers():
    resource = ResourceFactory()
    resource.area.open_weekdays = ALL_WEEKDAYS
    resource.area.save()
    client = ClientFactory(space=resource.space)
    PriceFactory(resource=resource, grid=client.pricing_grid)
    manager = MembershipFactory(space=resource.space, role=Role.MANAGER).user
    drafts = []
    for day in range(2, 8):
        book(resource, client, manager, day)
        drafts.append(services.create_draft(manager, resource.space, client, *NOV).invoice)
    barrier = threading.Barrier(len(drafts))

    def run(draft):
        barrier.wait()
        try:
            services.issue(manager, draft, dt.date(2026, 12, 1))
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(d,)) for d in drafts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    numbers = sorted(Invoice.objects.values_list("number", flat=True))
    assert numbers == [f"F2026-{i:04d}" for i in range(1, 7)]
