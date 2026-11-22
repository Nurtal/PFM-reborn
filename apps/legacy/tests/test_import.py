import datetime as dt
import sqlite3
from decimal import Decimal as D

import pytest
from django.core.management import call_command

from apps.accounts.models import User
from apps.booking.models import Booking, Status
from apps.clients.models import Client, PackagePrice, PricingGrid, ResourcePrice
from apps.core.models import Role, Space, SpaceMembership
from apps.core.tests.factories import UserFactory
from apps.invoices import services as invoicing
from apps.invoices.models import ImmutableInvoiceError, Invoice
from apps.legacy.importer import Importer
from apps.legacy.models import LegacyIdMap
from apps.legacy.sources import SQLiteSource
from apps.resources.models import Area, Authorization, Resource

pytestmark = pytest.mark.django_db

T0 = int(dt.datetime(2025, 3, 10, 8, tzinfo=dt.UTC).timestamp())
H = 3600

SCHEMA = {
    "core_spaces": "id, name, shortname, description, color, deleted",
    "core_users": "id, login, pwd, name, firstname, email, is_active, deleted",
    "core_j_spaces_user": "id, id_user, id_space, status, deleted",
    "cl_pricings": "id, id_space, name, display_order, deleted",
    "bk_nightwe": "id, id_belonging, tarif_unique, tarif_night, night_start, night_end, tarif_we, choice_we, id_space, deleted",
    "cl_addresses": "id, institution, building_floor, service, address, zip_code, city, country, deleted",
    "cl_clients": "id, id_space, name, contact_name, address_invoice, phone, email, pricing, deleted",
    "cl_j_client_user": "id, id_client, id_user, deleted",
    "re_area": "id, name, id_space, restricted, deleted",
    "bk_schedulings": "id, is_monday, is_tuesday, is_wednesday, is_thursday, is_friday, is_saturday, is_sunday, day_begin, day_end, size_bloc_resa, id_rearea, id_space, deleted",
    "re_category": "id, name, id_space, deleted",
    "re_info": "id, name, brand, type, description, long_description, id_category, id_area, id_space, display_order, deleted",
    "bk_authorization": "id, user_id, resource_id, date, date_desactivation, is_active, id_space, deleted",
    "bk_prices": "id, id_resource, id_package, day_night_we, id_belonging, price, id_space, deleted",
    "bk_packages": "id, id_package, id_resource, duration, name, id_space, deleted",
    "bk_j_packages_prices": "id_package, id_pricing, price, id_space, deleted",  # no id, as in prod
    "bk_calendar_entry": "id, start_time, end_time, resource_id, booked_by_id, recipient_id, short_description, full_description, package_id, responsible_id, invoice_id, id_space, deleted",
    "in_invoice": "id, number, id_space, period_begin, period_end, date_generated, date_paid, is_paid, id_responsible, total_ht, title, deleted",
    "in_invoice_item": "id, id_invoice, module, details, total_ht, id_space, deleted",
    "se_services": "id, id_space, name, description, display_order, is_active, deleted",
    "se_prices": "id, id_service, id_belonging, price, id_space, deleted",
    "se_order": "id, id_resp, id_space, id_user, id_status, date_open, date_close, no_identification, id_invoice, deleted",
    "se_order_service": "id, id_order, id_service, quantity, id_space, deleted",
    "se_project": "id, id_space, name, id_resp, id_user, date_open, date_close, time_limit, in_charge, samplescomment, deleted",
    "se_project_service": "id, id_project, id_service, date, quantity, comment, id_invoice, id_space, deleted",
}

ROWS = {
    "core_spaces": [
        (1, "Imagerie", "imaging", "Microscopy", "#112233", 0),
        (2, "Other", "other", "", "#000000", 0),
    ],
    "core_users": [
        (1, "alice", "md5hash", "Martin", "Alice", "alice@lab.fr", 1, 0),
        (2, "bob", "x", "Durand", "Bob", "ALICE@lab.fr", 1, 0),  # duplicate e-mail (case)
        (3, "carol", "x", "Old", "Carol", "", 0, 0),  # no e-mail, inactive
        (4, "ghost", "x", "", "", "ghost@lab.fr", 1, 1),  # deleted
    ],
    "core_j_spaces_user": [(1, 1, 1, 4, 0), (2, 2, 1, 2, 0), (3, 3, 1, 0, 0)],
    "cl_pricings": [(1, 1, "Académique", 0, 0)],
    "bk_nightwe": [(1, 1, 0, 1, 20, 7, 1, "0,0,0,0,0,1,1", 1, 0)],
    "cl_addresses": [
        (1, "Université", "Bât. 5", "UMR 1", "1 rue X", "35000", "Rennes", "France", 0)
    ],
    "cl_clients": [(1, 1, "Équipe Martin", "A. Martin", 1, "", "lab@lab.fr", 1, 0)],
    "cl_j_client_user": [(1, 1, 1, 0), (2, 1, 2, 0)],
    "re_area": [(1, "Salle 101", 1, 0, 0)],
    "bk_schedulings": [(1, 1, 1, 1, 1, 1, 0, 0, 8, 19, 1800, 1, 1, 0)],
    "re_category": [(1, "Confocaux", 1, 0)],
    "re_info": [
        (1, "LSM 980", "Zeiss", "Confocal", "Short", "Long text", 1, 1, 1, 0, 0),
        (2, "LSM 980", "Zeiss", "Old one", "", "", 1, 1, 1, 0, 1),  # deleted, same name
        (3, "Retired scope", "", "", "", "", 99, 98, 1, 0, 1),  # deleted, area/category gone
    ],
    "bk_authorization": [
        (1, 1, 1, "2020-01-01", "2021-01-01", 0, 1, 0),
        (2, 1, 1, "2023-05-01", "0000-00-00", 1, 1, 0),  # latest wins
    ],
    "bk_prices": [
        (1, 1, 0, "day", 1, "45,50", 1, 0),
        (2, 1, 0, "night", 1, "60", 1, 0),
        (3, 1, 0, "we", 1, "70", 1, 0),
    ],
    "bk_packages": [(1, 1, 1, "4.00", "Demi-journée", 1, 0)],
    "bk_j_packages_prices": [(1, 1, "150.00", 1, 0)],
    "bk_calendar_entry": [
        (1, T0, T0 + 2 * H, 1, 1, 1, "Run 1", "", 0, 1, 7, 1, 0),  # already invoiced
        (2, T0 + H, T0 + 3 * H, 1, 2, 2, "Overlap", "", 0, 1, 0, 1, 0),  # overlaps #1
        (3, T0 + 24 * H, T0 + 26 * H, 1, 2, 2, "Run 3", "", 0, 1, 0, 1, 0),
        (4, T0, T0 + H, 99, 1, 1, "Unknown resource", "", 0, 0, 0, 1, 0),
        (5, T0 - 48 * H, T0 - 46 * H, 2, 1, 1, "On retired", "", 0, 1, 0, 1, 0),
        (6, T0 - 96 * H, T0 - 95 * H, 3, 1, 1, "On orphan", "", 0, 1, 0, 1, 0),
    ],
    "in_invoice": [
        (
            7,
            "2025-0012",
            1,
            "2025-03-01",
            "2025-03-31",
            "2025-04-02",
            "2025-04-20",
            1,
            1,
            "80.00",
            "Mars",
            0,
        )
    ],
    "in_invoice_item": [(1, 7, "booking", "LSM 980 mars", "91.00", 1, 0)],
    "se_services": [(1, 1, "Préparation", "", 0, 1, 0)],
    "se_prices": [(1, 1, 1, "12,5", 1, 0)],
    "se_order": [
        (1, 1, 1, 1, 0, "2025-03-02", "2025-03-20", "BC-12", 4, 0),  # closed, billed by legacy
        (2, 1, 1, 2, 1, "2025-03-05", None, "", 0, 0),  # open, not billed
    ],
    "se_order_service": [(1, 1, 1, "3", 1, 0), (2, 2, 1, "2", 1, 0), (3, 2, 1, "abc", 1, 0)],
    "se_project": [(1, 1, "Atlas", 1, 1, "2025-01-10", None, "2025-06-30", 1, "", 0)],
    "se_project_service": [(1, 1, 1, "2025-03-12", "4", "coupes", 0, 1, 0)],
}


@pytest.fixture
def legacy_db(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    for table, cols in SCHEMA.items():
        conn.execute(f"CREATE TABLE {table} ({cols})")
        for row in ROWS.get(table, []):
            conn.execute(f"INSERT INTO {table} VALUES ({','.join('?' * len(row))})", row)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def admin():
    return UserFactory(is_superuser=True, is_staff=True)


def run(path, spaces=None):
    return Importer(SQLiteSource(str(path)), spaces).run()


def test_full_import(legacy_db, admin):
    report = run(legacy_db)
    space = Space.objects.get(slug="imaging")
    assert space.accent_color == "#112233"

    alice, bob = User.objects.get(username="alice"), User.objects.get(username="bob")
    assert not alice.has_usable_password()  # legacy md5 hashes are never imported
    assert bob.email.endswith("@legacy.invalid")  # duplicate e-mail replaced
    assert not User.objects.filter(username="ghost").exists()
    assert SpaceMembership.objects.get(user=alice, space=space).role == Role.ADMIN
    assert not SpaceMembership.objects.filter(user__username="carol").exists()  # inactive role

    grid = PricingGrid.objects.get(space=space)
    assert grid.night_rate_enabled and grid.weekend_rate_enabled
    assert (grid.night_start, grid.night_end, grid.weekend_days) == (
        dt.time(20),
        dt.time(7),
        0b1100000,
    )
    client = Client.objects.get(space=space)
    assert "Rennes" in client.invoice_address and set(client.users.all()) == {alice, bob}

    area = Area.objects.get(space=space, name="Salle 101")
    assert (area.open_weekdays, area.day_start, area.day_end, area.slot_minutes) == (
        0b0011111,
        dt.time(8),
        dt.time(19),
        30,
    )
    resource = Resource.objects.get(space=space, name="LSM 980")
    assert resource.description == "Short\n\nLong text"
    auth = Authorization.objects.get(user=alice)
    assert auth.granted_on == dt.date(2023, 5, 1) and auth.is_active

    price = ResourcePrice.objects.get(resource=resource)
    assert (price.day_rate, price.night_rate, price.weekend_rate) == (
        D("45.50"),
        D("60.00"),
        D("70.00"),
    )
    assert PackagePrice.objects.get().price == D("150.00")

    b1, b2, b3 = (
        Booking.objects.get(
            pk=LegacyIdMap.objects.get(legacy_table="bk_calendar_entry", legacy_id=i).new_id
        )
        for i in (1, 2, 3)
    )
    assert b1.status == Status.CONFIRMED and b1.billed_externally
    assert b2.status == Status.CANCELLED  # overlap
    assert b3.client == client and not b3.billed_externally
    assert report.skipped["bk_calendar_entry"] == 1

    invoice = Invoice.objects.get(number="2025-0012")
    assert invoice.is_issued and invoice.paid_at == dt.date(2025, 4, 20)
    assert invoice.total_excl_tax == D("80.00")
    assert sum(line.amount for line in invoice.lines.all()) == D("80.00")  # discount line added
    with pytest.raises(ImmutableInvoiceError):
        invoice.save()
    assert any("overlaps" in a for a in report.anomalies)


def test_rerun_is_idempotent(legacy_db, admin):
    run(legacy_db)
    counts = (
        User.objects.count(),
        Booking.objects.count(),
        Invoice.objects.count(),
        Client.objects.count(),
    )
    report = run(legacy_db)
    assert (
        User.objects.count(),
        Booking.objects.count(),
        Invoice.objects.count(),
        Client.objects.count(),
    ) == counts
    assert report.created["core_users"] == 0 and report.skipped["in_invoice"] == 1


def test_imported_legacy_billing_is_not_billed_again(legacy_db, admin):
    run(legacy_db)
    space = Space.objects.get(slug="imaging")
    client = Client.objects.get(space=space)
    result = invoicing.create_draft(admin, space, client, dt.date(2025, 3, 1), dt.date(2025, 3, 31))
    # Only booking 3 (not invoiced by the legacy system) is billable.
    assert result.invoice.lines.filter(booking__title="Run 3").exists()
    assert not result.invoice.lines.filter(booking__title="Run 1").exists()


def test_single_space_import(legacy_db, admin):
    run(legacy_db, {2})
    assert list(Space.objects.values_list("slug", flat=True)) == ["other"]
    assert not User.objects.filter(username="alice").exists()


def test_command_dry_run_saves_nothing(legacy_db, admin, settings):
    call_command("import_legacy", source=f"sqlite:///{legacy_db}", dry_run=True)
    assert not Space.objects.filter(slug="imaging").exists()
    assert not LegacyIdMap.objects.exists()
    assert list(settings.DATA_DIR.glob("legacy-import-*-dry.json"))


def test_source_refuses_unknown_tables(legacy_db):
    with pytest.raises(ValueError):
        list(SQLiteSource(str(legacy_db)).rows("core_users; DROP TABLE x"))


def test_rerun_does_not_resurrect_an_erased_account(legacy_db, admin):
    from apps.accounts.services import anonymize

    run(legacy_db)
    bob = User.objects.get(username="bob")
    anonymize(bob)
    run(legacy_db)
    bob.refresh_from_db()
    assert not bob.is_active and bob.username.startswith("deleted-") and bob.last_name == ""
    assert not Client.objects.filter(users=bob).exists()
    assert not SpaceMembership.objects.filter(user=bob).exists()


def test_services_orders_and_projects(legacy_db, admin):
    from apps.services.models import Order, OrderLine, Project, ServicePrice

    report = run(legacy_db)
    assert ServicePrice.objects.get().price == D("12.50")
    closed = Order.objects.get(title="BC-12")
    assert closed.status == "done" and closed.lines.get().billed_externally
    open_order = Order.objects.get(title="Order 2")
    assert open_order.lines.count() == 1  # the "abc" quantity is skipped
    assert report.skipped["se_order_service"] == 1
    project = Project.objects.get(name="Atlas")
    assert (project.start_date, project.end_date, project.status) == (
        dt.date(2025, 1, 10),
        dt.date(2025, 6, 30),
        "active",
    )
    line = OrderLine.objects.get(order__project=project)
    assert (line.quantity, line.performed_on, line.comment) == (
        D("4.00"),
        dt.date(2025, 3, 12),
        "coupes",
    )
    # Only what the legacy system did not invoice is billable now.
    space = Space.objects.get(slug="imaging")
    client = Client.objects.get(space=space)
    draft = invoicing.create_draft(
        admin, space, client, dt.date(2025, 3, 1), dt.date(2025, 3, 31)
    ).invoice
    amounts = sorted(line.amount for line in draft.lines.filter(order_line__isnull=False))
    assert amounts == [D("25.00"), D("50.00")]  # order 2 (2 x 12.50) and project (4 x 12.50)
    run(legacy_db)
    assert OrderLine.objects.count() == 3  # idempotent


def test_retired_resources_keep_their_history(legacy_db, admin):
    report = run(legacy_db)
    retired = Resource.objects.get(name="LSM 980 (#2)")  # same name as the live one
    assert not retired.is_active
    assert Booking.objects.filter(resource=retired, title="On retired").exists()
    orphan = Resource.objects.get(name="Retired scope")
    assert orphan.area.name == "Archives (import)" and orphan.area.is_restricted
    assert Booking.objects.filter(resource=orphan).exists()
    assert any("1 booking(s) skipped: unknown resource" in a for a in report.anomalies)
    run(legacy_db)  # names stay stable on re-run
    assert Resource.objects.filter(name__startswith="LSM 980").count() == 2
