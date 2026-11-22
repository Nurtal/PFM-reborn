import pytest
from allauth.mfa.models import Authenticator
from django.test import override_settings
from django.urls import reverse

from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory, UserFactory

pytestmark = pytest.mark.django_db


@override_settings(PFM_REQUIRE_ADMIN_MFA=True)
def test_superuser_without_mfa_is_forced_to_enrol(client, setup_done):
    root = UserFactory(is_superuser=True, is_staff=True)
    client.force_login(root)
    response = client.get(reverse("core:home"))
    assert response.status_code == 302
    assert response["Location"] == reverse("mfa_activate_totp")


@override_settings(PFM_REQUIRE_ADMIN_MFA=True)
def test_space_admin_without_mfa_is_forced_to_enrol(client, setup_done):
    m = MembershipFactory(role=Role.ADMIN)
    client.force_login(m.user)
    response = client.get(reverse("core:space_dashboard", args=[m.space.slug]))
    assert response["Location"] == reverse("mfa_activate_totp")


def test_mfa_setup_page_stays_reachable(client, setup_done):
    root = UserFactory(is_superuser=True, is_staff=True)
    client.force_login(root)
    response = client.get(reverse("mfa_activate_totp"))
    # allauth asks for the password again (fresh login) but never loops back to us.
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("account_reauthenticate"))


def test_admin_with_totp_passes(client, setup_done):
    m = MembershipFactory(role=Role.ADMIN)
    Authenticator.objects.create(user=m.user, type=Authenticator.Type.TOTP, data={})
    client.force_login(m.user)
    assert client.get(reverse("core:space_dashboard", args=[m.space.slug])).status_code == 200


def test_regular_user_does_not_need_mfa(client, setup_done):
    m = MembershipFactory(role=Role.USER)
    client.force_login(m.user)
    assert client.get(reverse("core:space_dashboard", args=[m.space.slug])).status_code == 200


def test_login_page_renders(client, setup_done):
    response = client.get(reverse("account_login"))
    assert response.status_code == 200
    assert b"btn-primary" in response.content


@override_settings(PFM_REQUIRE_ADMIN_MFA=True)
def test_promotion_to_admin_is_caught_after_recheck_window(client, setup_done, monkeypatch):
    import time

    from apps.accounts import middleware

    m = MembershipFactory(role=Role.USER)
    client.force_login(m.user)
    url = reverse("core:space_dashboard", args=[m.space.slug])
    assert client.get(url).status_code == 200

    m.role = Role.ADMIN
    m.save()
    assert client.get(url).status_code == 200  # still inside the window

    now = time.time() + middleware.RECHECK_SECONDS + 1
    monkeypatch.setattr(middleware.time, "time", lambda: now)
    assert client.get(url)["Location"] == reverse("mfa_activate_totp")


def test_admin_mfa_is_optional_by_default(client, setup_done):
    root = UserFactory(is_superuser=True, is_staff=True)
    client.force_login(root)
    response = client.get(reverse("core:home"))
    assert response.get("Location") != reverse("mfa_activate_totp")
    m = MembershipFactory(role=Role.ADMIN)
    client.force_login(m.user)
    assert client.get(reverse("core:space_dashboard", args=[m.space.slug])).status_code == 200
