import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.core import services
from apps.core.models import InstanceSettings, Role, SpaceMembership

pytestmark = pytest.mark.django_db

VALID = {
    "site_name": "PFM",
    "username": "admin",
    "email": "admin@example.org",
    "password1": "a-long-and-unusual-passphrase",
    "password2": "a-long-and-unusual-passphrase",
    "space_name": "Imaging",
    "space_slug": "imaging",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_security": "starttls",
}


def test_every_page_redirects_to_setup_until_done(client):
    response = client.get("/")
    assert response.status_code == 302
    assert response["Location"] == reverse("core:setup")


def test_healthz_is_reachable_before_setup(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_setup_requires_the_token(client):
    response = client.post(reverse("core:setup"), {**VALID, "token": "wrong"})
    assert response.status_code == 200
    assert not User.objects.exists()
    assert "token" in response.context["form"].errors


def test_setup_creates_admin_space_and_membership(client, django_capture_on_commit_callbacks):
    token = services.setup_token()
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("core:setup"), {**VALID, "token": token})

    assert response.status_code == 302
    assert response["Location"] == reverse("core:home")  # 2FA optional by default
    admin = User.objects.get(username="admin")
    assert admin.is_superuser
    assert admin.check_password(VALID["password1"])
    membership = SpaceMembership.objects.get(user=admin)
    assert membership.space.slug == "imaging"
    assert membership.role == Role.ADMIN
    assert InstanceSettings.load().setup_completed_at is not None
    assert not (settings.DATA_DIR / services.SETUP_TOKEN_FILENAME).exists()
    # The admin can enrol 2FA immediately, without retyping the password.
    assert client.get(reverse("mfa_activate_totp")).status_code == 200


def test_setup_cannot_run_twice(client):
    client.post(reverse("core:setup"), {**VALID, "token": services.setup_token()})
    client.logout()
    response = client.post(reverse("core:setup"), {**VALID, "username": "intruder", "token": "x"})
    assert response.status_code == 302
    assert not User.objects.filter(username="intruder").exists()


def test_setup_rejects_weak_password(client):
    data = {**VALID, "password1": "short", "password2": "short", "token": services.setup_token()}
    response = client.post(reverse("core:setup"), data)
    assert "password1" in response.context["form"].errors


def test_smtp_password_is_encrypted_at_rest(client):
    data = {**VALID, "smtp_host": "smtp.example.org", "smtp_password": "s3cret"}
    client.post(reverse("core:setup"), {**data, "token": services.setup_token()})
    instance = InstanceSettings.load()
    assert "s3cret" not in instance.smtp_password_encrypted
    assert instance.get_smtp_password() == "s3cret"


def test_smtp_test_endpoint_needs_token_before_setup(client):
    response = client.post(
        reverse("core:setup_test_smtp"),
        {"smtp_host": "localhost", "smtp_port": 25, "smtp_security": "none", "email": "a@b.c"},
    )
    assert b"Invalid setup token" in response.content or "invalide" in response.content.decode()


def test_setup_page_in_french(client):
    response = client.get(reverse("core:setup"), HTTP_ACCEPT_LANGUAGE="fr")
    assert response.status_code == 200
    assert "Bienvenue dans PFM" in response.content.decode() or not _french_compiled()


def _french_compiled():
    return (settings.BASE_DIR / "locale/fr/LC_MESSAGES/django.mo").exists()


@override_settings(PFM_REQUIRE_ADMIN_MFA=True)
def test_setup_sends_admin_to_2fa_when_mandatory(client):
    response = client.post(reverse("core:setup"), {**VALID, "token": services.setup_token()})
    assert response["Location"] == reverse("mfa_activate_totp")
