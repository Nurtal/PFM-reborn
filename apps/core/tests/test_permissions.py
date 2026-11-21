"""Space isolation and role checks (README §7.5)."""

import pytest
from allauth.mfa.models import Authenticator
from django.urls import reverse

from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory, SpaceFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_spaces(setup_done):
    a, b = SpaceFactory(slug="a"), SpaceFactory(slug="b")
    return a, b


def test_anonymous_is_sent_to_login(client, setup_done):
    space = SpaceFactory()
    response = client.get(reverse("core:space_dashboard", args=[space.slug]))
    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


@pytest.mark.parametrize("name", ["core:space_dashboard", "core:members"])
def test_member_of_a_sees_nothing_of_b(client, two_spaces, name):
    a, b = two_spaces
    member = MembershipFactory(space=a, role=Role.ADMIN).user
    _login(client, member)
    assert client.get(reverse(name, args=[b.slug])).status_code == 404


def test_member_of_a_cannot_edit_membership_of_b_by_guessing_ids(client, two_spaces):
    a, b = two_spaces
    attacker = MembershipFactory(space=a, role=Role.ADMIN).user
    victim = MembershipFactory(space=b, role=Role.USER)
    _login(client, attacker)
    # Right space in the URL, foreign membership id: still 404.
    url = reverse("core:member_detail", args=[a.slug, victim.pk])
    assert client.post(url, {"action": "remove"}).status_code == 404
    victim.refresh_from_db()  # raises if it was deleted


def test_visitor_sees_dashboard_but_not_members(client, setup_done):
    m = MembershipFactory(role=Role.VISITOR)
    _login(client, m.user)
    assert client.get(reverse("core:space_dashboard", args=[m.space.slug])).status_code == 200
    assert client.get(reverse("core:members", args=[m.space.slug])).status_code == 404


def test_admin_manages_members(client, setup_done):
    admin_m = MembershipFactory(role=Role.ADMIN)
    newcomer = UserFactory()
    _login(client, admin_m.user)
    url = reverse("core:members", args=[admin_m.space.slug])
    assert client.get(url).status_code == 200
    response = client.post(url, {"email": newcomer.email, "role": Role.MANAGER})
    assert response.status_code == 302
    assert newcomer.memberships.get().role == Role.MANAGER


def test_inactive_space_is_hidden_from_members(client, setup_done):
    m = MembershipFactory(role=Role.ADMIN)
    m.space.is_active = False
    m.space.save()
    _login(client, m.user)
    assert client.get(reverse("core:space_dashboard", args=[m.space.slug])).status_code == 404


def _login(client, user):
    # Admins must have a second factor (enforcement itself is tested in accounts).
    Authenticator.objects.get_or_create(user=user, type=Authenticator.Type.TOTP, data={})
    client.force_login(user)


def test_space_settings_for_admins_only(client, setup_done):
    m = MembershipFactory(role=Role.MANAGER)
    _login(client, m.user)
    url = reverse("core:space_settings", args=[m.space.slug])
    assert client.get(url).status_code == 404
    admin = MembershipFactory(space=m.space, role=Role.ADMIN).user
    _login(client, admin)
    assert client.get(url).status_code == 200
    response = client.post(
        url,
        {
            "name": "Renamed",
            "description": "",
            "timezone": "Europe/Paris",
            "currency": "chf",
            "accent_color": "#123456",
            "catalogue_public": "on",
        },
    )
    assert response.status_code == 302
    m.space.refresh_from_db()
    assert (m.space.name, m.space.currency, m.space.catalogue_public) == ("Renamed", "CHF", True)
