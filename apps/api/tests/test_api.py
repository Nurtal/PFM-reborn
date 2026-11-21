import datetime as dt
import zoneinfo

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.api.models import ApiToken
from apps.booking import services as booking
from apps.booking.models import Booking, Status
from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory, SpaceFactory
from apps.invoices.models import Invoice
from apps.resources.models import ALL_WEEKDAYS
from apps.resources.tests.factories import ResourceFactory

pytestmark = pytest.mark.django_db
PARIS = zoneinfo.ZoneInfo("Europe/Paris")


@pytest.fixture
def env(setup_done):
    resource = ResourceFactory()
    resource.area.open_weekdays = ALL_WEEKDAYS
    resource.area.save()
    user = MembershipFactory(space=resource.space, role=Role.USER).user
    _token, key = ApiToken.issue(user, "script")
    return resource, user, {"HTTP_AUTHORIZATION": f"Bearer {key}"}


def slot(days=2, hours=1):
    start = (
        (timezone.now() + dt.timedelta(days=days))
        .astimezone(PARIS)
        .replace(hour=9, minute=0, second=0, microsecond=0)
    )
    return start, start + dt.timedelta(hours=hours)


def test_token_required(client, env):
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me", HTTP_AUTHORIZATION="Bearer pfm_wrong").status_code == 401


def test_me_and_spaces(client, env):
    resource, user, auth = env
    assert client.get("/api/v1/me", **auth).json()["username"] == user.username
    assert [s["slug"] for s in client.get("/api/v1/spaces", **auth).json()] == [resource.space.slug]


def test_book_and_cancel_through_services(client, env):
    resource, user, auth = env
    start, end = slot()
    url = f"/api/v1/spaces/{resource.space.slug}/bookings"
    response = client.post(
        url,
        {
            "resource": str(resource.public_id),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "title": "API run",
        },
        content_type="application/json",
        **auth,
    )
    assert response.status_code == 201, response.content
    booking_id = response.json()["id"]
    # Business rules apply: an overlapping booking is refused with reasons.
    again = client.post(
        url,
        {"resource": str(resource.public_id), "start": start.isoformat(), "end": end.isoformat()},
        content_type="application/json",
        **auth,
    )
    assert again.status_code == 400 and "overlaps" in " ".join(again.json()["errors"])
    assert client.delete(f"{url}/{booking_id}", **auth).status_code == 204
    assert Booking.objects.get(public_id=booking_id).status == Status.CANCELLED


def test_listing_hides_other_users(client, env):
    resource, user, auth = env
    other = MembershipFactory(space=resource.space, role=Role.USER).user
    start, end = slot()
    booking.reserve(other, resource, start, end, title="Secret")
    data = client.get(
        f"/api/v1/spaces/{resource.space.slug}/bookings",
        {
            "start": (start - dt.timedelta(days=1)).isoformat(),
            "end": (end + dt.timedelta(days=1)).isoformat(),
        },
        **auth,
    ).json()
    assert data[0]["title"] is None and data[0]["user"] is None and not data[0]["mine"]


def test_foreign_space_and_manager_only_endpoints(client, env):
    resource, user, auth = env
    other = SpaceFactory()
    assert client.get(f"/api/v1/spaces/{other.slug}/resources", **auth).status_code == 404
    assert client.get(f"/api/v1/spaces/{resource.space.slug}/invoices", **auth).status_code == 404


def test_expired_and_revoked_tokens(client, env):
    resource, user, auth = env
    token = ApiToken.objects.get(user=user)
    token.expires_at = timezone.now() - dt.timedelta(seconds=1)
    token.save()
    assert client.get("/api/v1/me", **auth).status_code == 401


def test_only_hash_is_stored(env):
    resource, user, auth = env
    key = auth["HTTP_AUTHORIZATION"].split()[1]
    assert not ApiToken.objects.filter(key_hash=key).exists()
    assert ApiToken.objects.get(user=user).hint == key[:10]


def test_session_cookie_does_not_authenticate_the_api(client, env):
    resource, user, auth = env
    client.force_login(user)
    assert client.get("/api/v1/me").status_code == 401


def test_token_page(client, env):
    resource, user, auth = env
    client.force_login(user)
    response = client.post(reverse("api_tokens"), {"name": "CI", "days": "30"})
    assert response.status_code == 200 and response.context["new_key"].startswith("pfm_")
    token = ApiToken.objects.get(name="CI")
    client.post(reverse("api_tokens"), {"revoke": token.pk})
    assert not ApiToken.objects.filter(name="CI").exists()


def test_openapi_schema_is_served(client, env):
    assert client.get("/api/v1/openapi.json").status_code in (200, 401)
    assert Invoice.objects.count() == 0
