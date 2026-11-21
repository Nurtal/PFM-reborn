import datetime as dt
import threading
import zoneinfo

import pytest
from django.core import mail
from django.db import connection

from apps.booking import services
from apps.booking.models import Booking, Closure, Status
from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory, UserFactory
from apps.resources.models import Access
from apps.resources.tests.factories import AuthorizationFactory, ResourceFactory

pytestmark = pytest.mark.django_db
PARIS = zoneinfo.ZoneInfo("Europe/Paris")
NOW = dt.datetime(2026, 11, 2, 7, 0, tzinfo=PARIS)


def at(h, mi=0, day=3):
    return dt.datetime(2026, 11, day, h, mi, tzinfo=PARIS)


@pytest.fixture
def resource():
    return ResourceFactory()


def member(resource, role=Role.USER):
    return MembershipFactory(space=resource.space, role=role).user


def book(user, resource, start, end, **kw):
    return services.reserve(user, resource, start, end, now=NOW, **kw)


def test_user_books_a_free_slot(resource):
    user = member(resource)
    booking = book(user, resource, at(9), at(10))
    assert booking.user == booking.created_by == user
    assert booking.start == at(9)
    assert booking.status == Status.CONFIRMED


def test_visitor_cannot_book(resource):
    with pytest.raises(services.BookingError):
        book(member(resource, Role.VISITOR), resource, at(9), at(10))


def test_non_member_cannot_book(resource):
    with pytest.raises(services.BookingError):
        book(UserFactory(), resource, at(9), at(10))


def test_overlap_is_refused(resource):
    book(member(resource), resource, at(9), at(11))
    with pytest.raises(services.BookingError, match="overlaps"):
        book(member(resource), resource, at(10), at(12))


def test_adjacent_slots_are_fine(resource):
    book(member(resource), resource, at(9), at(10))
    book(member(resource), resource, at(10), at(11))
    assert Booking.objects.count() == 2


def test_cancelled_booking_frees_the_slot(resource):
    user = member(resource)
    first = book(user, resource, at(9), at(10))
    services.cancel(user, first, now=NOW)
    book(member(resource), resource, at(9), at(10))


def test_overlap_on_other_resource_is_fine(resource):
    other = ResourceFactory(space=resource.space, area=resource.area, category=resource.category)
    book(member(resource), resource, at(9), at(10))
    book(member(resource), other, at(9), at(10))


def test_closure_blocks_resource_and_whole_area(resource):
    Closure.objects.create(
        space=resource.space, resource=resource, start=at(9), end=at(12), reason="Maintenance"
    )
    with pytest.raises(services.BookingError, match="Maintenance"):
        book(member(resource), resource, at(10), at(11))
    Closure.objects.create(
        space=resource.space, area=resource.area, start=at(14), end=at(15), reason="Holiday"
    )
    with pytest.raises(services.BookingError, match="Holiday"):
        book(member(resource), resource, at(14), at(15))


def test_authorization_required(resource):
    resource.access = Access.AUTHORIZED
    resource.save()
    user = member(resource)
    with pytest.raises(services.BookingError, match="authorization"):
        book(user, resource, at(9), at(10))
    AuthorizationFactory(user=user, category=resource.category, granted_on=dt.date(2026, 1, 1))
    book(user, resource, at(9), at(10))


def test_expired_authorization_is_not_enough(resource):
    resource.access = Access.AUTHORIZED
    resource.save()
    user = member(resource)
    AuthorizationFactory(
        user=user,
        category=resource.category,
        granted_on=dt.date(2025, 1, 1),
        expires_on=dt.date(2025, 12, 31),
    )
    with pytest.raises(services.BookingError):
        book(user, resource, at(9), at(10))


def test_managers_only_resource(resource):
    resource.access = Access.MANAGERS
    resource.save()
    with pytest.raises(services.BookingError):
        book(member(resource), resource, at(9), at(10))
    book(member(resource, Role.MANAGER), resource, at(9), at(10))


def test_resource_manager_counts_as_manager(resource):
    resource.access = Access.MANAGERS
    resource.save()
    user = member(resource)
    resource.managers.add(user)
    book(user, resource, at(9), at(10))


def test_manager_bypasses_rules_but_not_overlaps(resource):
    manager = member(resource, Role.MANAGER)
    book(manager, resource, at(6), at(23))  # outside opening hours: allowed
    with pytest.raises(services.BookingError, match="overlaps"):
        book(manager, resource, at(22), at(23, 30))


def test_only_managers_book_for_someone_else(resource):
    user, other = member(resource), member(resource)
    with pytest.raises(services.BookingError):
        book(user, resource, at(9), at(10), for_user=other)
    booking = book(member(resource, Role.MANAGER), resource, at(9), at(10), for_user=other)
    assert booking.user == other


def test_daily_quota(resource):
    resource.max_bookings_per_day = 1
    resource.save()
    user = member(resource)
    book(user, resource, at(9), at(10))
    with pytest.raises(services.BookingError, match="per day"):
        book(user, resource, at(14), at(15))
    book(user, resource, at(9, day=4), at(10, day=4))  # next day is fine


def test_change_deadline(resource):
    resource.edit_deadline_hours = 24
    resource.save()
    user = member(resource)
    booking = book(user, resource, at(9), at(10))
    late = at(10, day=2)  # 23 h before start
    assert not services.can_change(user, booking, late)
    with pytest.raises(services.BookingError):
        services.cancel(user, booking, now=late)
    assert services.can_change(member(resource, Role.MANAGER), booking, late)


def test_others_cannot_change_my_booking(resource):
    booking = book(member(resource), resource, at(9), at(10))
    assert not services.can_change(member(resource), booking, NOW)


def test_modify_moves_booking_and_rechecks_overlap(resource):
    user = member(resource)
    booking = book(user, resource, at(9), at(10))
    book(member(resource), resource, at(12), at(13))
    services.modify(user, booking, at(10), at(11), now=NOW)  # overlaps its own old slot: fine
    booking.refresh_from_db()
    assert booking.start == at(10)
    with pytest.raises(services.BookingError, match="overlaps"):
        services.modify(user, booking, at(12), at(13), now=NOW)


def test_history_is_kept(resource):
    user = member(resource)
    booking = book(user, resource, at(9), at(10))
    services.cancel(user, booking, now=NOW)
    assert booking.history.count() == 2


def test_confirmation_email(resource, django_capture_on_commit_callbacks):
    user = member(resource)
    with django_capture_on_commit_callbacks(execute=True):
        book(user, resource, at(9), at(10))
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert resource.name in mail.outbox[0].subject


@pytest.mark.django_db(transaction=True)
def test_concurrent_reservations_cannot_double_book():
    """Many threads race for the same slot: exactly one wins (README §6)."""
    resource = ResourceFactory()
    users = [member(resource) for _ in range(8)]
    barrier = threading.Barrier(len(users))
    results: list[str] = []

    def attempt(user):
        barrier.wait()
        try:
            book(user, resource, at(9), at(10))
            results.append("ok")
        except services.BookingError:
            results.append("refused")
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt, args=(u,)) for u in users]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 1, results
    assert Booking.objects.filter(resource=resource, status=Status.CONFIRMED).count() == 1
