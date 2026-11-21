import datetime as dt
import json
import zoneinfo

import pytest
from allauth.mfa.models import Authenticator
from django.urls import reverse
from django.utils import timezone

from apps.booking import services
from apps.booking.models import Booking, FeedToken, Status
from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory, SpaceFactory
from apps.resources.models import ALL_WEEKDAYS
from apps.resources.tests.factories import AreaFactory, ResourceFactory

pytestmark = pytest.mark.django_db
PARIS = zoneinfo.ZoneInfo("Europe/Paris")


@pytest.fixture
def resource(setup_done):
    space = SpaceFactory()
    area = AreaFactory(
        space=space, open_weekdays=ALL_WEEKDAYS, day_start=dt.time(0), day_end=dt.time(0)
    )
    return ResourceFactory(space=space, area=area)


def login(client, resource, role=Role.USER):
    user = MembershipFactory(space=resource.space, role=role).user
    if role >= Role.ADMIN:
        Authenticator.objects.create(user=user, type=Authenticator.Type.TOTP, data={})
    client.force_login(user)
    return user


def future(hours=24, length=1):
    base = (
        (timezone.now() + dt.timedelta(hours=hours))
        .astimezone(PARIS)
        .replace(minute=0, second=0, microsecond=0)
    )
    return base, base + dt.timedelta(hours=length)


def url(name, resource, **kw):
    return reverse(f"booking:{name}", kwargs={"space_slug": resource.space.slug, **kw})


def test_calendar_page(client, resource):
    login(client, resource)
    response = client.get(url("calendar", resource, resource_id=resource.public_id))
    assert response.status_code == 200
    assert b'id="calendar-config"' in response.content
    assert b'name="csp-nonce"' in response.content


def test_calendar_of_other_space_is_404(client, resource):
    other = ResourceFactory()
    login(client, resource)
    response = client.get(
        reverse(
            "booking:calendar",
            kwargs={"space_slug": resource.space.slug, "resource_id": other.public_id},
        )
    )
    assert response.status_code == 404


def test_events_feed_hides_other_users_names(client, resource):
    owner = MembershipFactory(space=resource.space, role=Role.USER).user
    start, end = future()
    services.reserve(owner, resource, start, end, title="Secret experiment")
    login(client, resource)
    response = client.get(
        url("events", resource, resource_id=resource.public_id),
        {
            "start": (start - dt.timedelta(days=1)).isoformat(),
            "end": (end + dt.timedelta(days=1)).isoformat(),
        },
    )
    events = response.json()
    assert len(events) == 1
    assert "Secret" not in json.dumps(events)
    assert str(owner) not in json.dumps(events)
    assert events[0]["extendedProps"]["detailUrl"] is None


def test_events_feed_rejects_huge_ranges(client, resource):
    login(client, resource)
    response = client.get(
        url("events", resource, resource_id=resource.public_id),
        {"start": "2026-01-01T00:00:00+00:00", "end": "2027-01-01T00:00:00+00:00"},
    )
    assert response.status_code == 400


def test_book_through_dialog(client, resource):
    user = login(client, resource)
    start, end = future()
    fmt = "%Y-%m-%dT%H:%M"
    response = client.post(
        url("create", resource, resource_id=resource.public_id),
        {"start": start.strftime(fmt), "end": end.strftime(fmt), "title": "Run 1"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 204
    assert json.loads(response["HX-Trigger"])["bookingChanged"]
    booking = Booking.objects.get()
    assert booking.user == user
    # The naive datetime-local value was read in the platform time zone.
    assert booking.start == start


def test_refused_booking_shows_errors(client, resource):
    login(client, resource, Role.VISITOR)
    start, end = future()
    fmt = "%Y-%m-%dT%H:%M"
    response = client.post(
        url("create", resource, resource_id=resource.public_id),
        {"start": start.strftime(fmt), "end": end.strftime(fmt)},
    )
    assert response.status_code == 200
    assert not Booking.objects.exists()
    assert response.context["form"].non_field_errors()


def test_owner_cancels_from_dialog(client, resource):
    user = login(client, resource)
    booking = services.reserve(user, resource, *future())
    response = client.post(
        url("detail", resource, booking_id=booking.public_id), {"action": "cancel"}
    )
    assert response.status_code == 204
    booking.refresh_from_db()
    assert booking.status == Status.CANCELLED


def test_other_user_cannot_open_or_cancel(client, resource):
    owner = MembershipFactory(space=resource.space, role=Role.USER).user
    booking = services.reserve(owner, resource, *future())
    login(client, resource)
    detail = url("detail", resource, booking_id=booking.public_id)
    assert client.get(detail).status_code == 404
    assert client.post(detail, {"action": "cancel"}).status_code == 404
    booking.refresh_from_db()
    assert booking.status == Status.CONFIRMED


def test_cannot_move_booking_past_its_deadline_by_posting_a_later_start(client, resource):
    """Regression: the deadline is computed on the stored start, not the posted one."""
    resource.edit_deadline_hours = 48
    resource.save()
    user = login(client, resource)
    booking = services.reserve(user, resource, *future(hours=24))  # already inside the deadline
    start, end = future(hours=24 * 10)
    fmt = "%Y-%m-%dT%H:%M"
    response = client.post(
        url("detail", resource, booking_id=booking.public_id),
        {"start": start.strftime(fmt), "end": end.strftime(fmt)},
    )
    assert response.status_code == 200
    booking.refresh_from_db()
    assert booking.start != start


def test_my_bookings_and_ical_feed(client, resource):
    user = login(client, resource)
    services.reserve(user, resource, *future(), title="Mine; with, specials")
    response = client.get(url("mine", resource))
    assert response.status_code == 200
    token = FeedToken.objects.get(user=user).token
    client.logout()
    feed = client.get(reverse("ical_feed", args=[token]))
    assert feed.status_code == 200
    body = feed.content.decode()
    assert body.startswith("BEGIN:VCALENDAR\r\n")
    assert r"Mine\; with\, specials" in body
    assert client.get(reverse("ical_feed", args=["wrong"])).status_code == 404


def test_manager_pages_require_manager(client, resource):
    login(client, resource)
    space = resource.space.slug
    for name in ["resources:create", "resources:settings", "resources:authorizations"]:
        assert client.get(reverse(name, args=[space])).status_code == 404, name
    assert client.get(reverse("resources:list", args=[space])).status_code == 200


def test_manager_creates_resource_only_with_own_space_choices(client, resource):
    login(client, resource, Role.MANAGER)
    foreign = ResourceFactory()
    response = client.get(reverse("resources:create", args=[resource.space.slug]))
    assert response.status_code == 200
    areas = list(response.context["form"].fields["area"].queryset)
    assert resource.area in areas and foreign.area not in areas
    response = client.post(
        reverse("resources:create", args=[resource.space.slug]),
        {
            "name": "Sneaky",
            "category": foreign.category.pk,  # another platform's category
            "area": resource.area.pk,
            "color": "#000000",
            "order": 0,
            "access": "users",
            "min_duration_minutes": 0,
            "edit_deadline_hours": 0,
        },
    )
    assert response.status_code == 200
    assert "category" in response.context["form"].errors
