import pytest
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_argon2_is_the_default_hasher():
    from config.settings import base

    assert base.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")


def test_security_headers(client, setup_done):
    response = client.get(reverse("account_login"))
    assert response.status_code == 200
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Permissions-Policy"] == settings.PERMISSIONS_POLICY


def test_self_signup_is_closed(client, setup_done):
    response = client.get(reverse("account_signup"))
    assert "account/signup_closed.html" in [t.name for t in response.templates]


def test_prod_settings_refuse_debug(monkeypatch):
    import importlib

    from django.core.exceptions import ImproperlyConfigured

    monkeypatch.setenv("DJANGO_DEBUG", "true")
    with pytest.raises(ImproperlyConfigured):
        import config.settings.prod as prod

        importlib.reload(prod)


def test_login_page_does_not_invite_to_sign_up(client, setup_done):
    response = client.get(reverse("account_login"))
    assert reverse("account_signup").encode() not in response.content
