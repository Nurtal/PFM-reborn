import pytest
from allauth.socialaccount.models import SocialApp
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def provider():
    return SocialApp.objects.create(
        provider="openid_connect",
        provider_id="renater",
        name="Renater",
        client_id="pfm",
        secret="secret",
        settings={"server_url": "https://idp.example.org/.well-known/openid-configuration"},
    )


def test_login_page_offers_the_institutional_sso(client, setup_done, provider):
    response = client.get(reverse("account_login"))
    assert response.status_code == 200
    assert "Renater" in response.content.decode()


def test_sso_entry_point_is_reachable_anonymously(client, setup_done, provider):
    url = reverse("openid_connect_login", kwargs={"provider_id": "renater"})
    response = client.get(url)
    # allauth asks to confirm with a POST (CSRF-safe); it must not bounce to the login page.
    assert response.status_code == 200


def test_sso_signup_is_open_even_when_local_signup_is_closed():
    from apps.accounts.adapters import AccountAdapter, SocialAccountAdapter

    assert SocialAccountAdapter().is_open_for_signup(None, None) is True
    assert AccountAdapter().is_open_for_signup(None) is False
