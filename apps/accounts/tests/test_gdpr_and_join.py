import datetime as dt
import json

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.booking import services as booking
from apps.booking.models import Booking
from apps.core import services
from apps.core.models import InstanceSettings, JoinRequest, JoinStatus, Role, SpaceMembership
from apps.core.tests.factories import MembershipFactory, SpaceFactory, UserFactory
from apps.resources.models import ALL_WEEKDAYS
from apps.resources.tests.factories import ResourceFactory

pytestmark = pytest.mark.django_db


def verified(user):
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    return user


def test_join_request_flow(client, setup_done, django_capture_on_commit_callbacks):
    space = SpaceFactory()
    admin = MembershipFactory(space=space, role=Role.ADMIN).user
    user = verified(UserFactory())
    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("core:join", args=[space.slug]), {"message": "PhD student"})
    assert response.status_code == 302
    join = JoinRequest.objects.get()
    assert join.status == JoinStatus.PENDING
    assert mail.outbox[-1].to == [admin.email]
    with pytest.raises(services.JoinError):
        services.request_join(user, space)  # already pending
    with django_capture_on_commit_callbacks(execute=True):
        services.decide_join(admin, join, approve=True)
    assert SpaceMembership.objects.get(user=user, space=space).role == Role.USER
    assert mail.outbox[-1].to == [user.email]
    with pytest.raises(services.JoinError):
        services.decide_join(admin, join, approve=False)


def test_join_requires_verified_email_and_open_space():
    space = SpaceFactory()
    with pytest.raises(services.JoinError, match="e-mail"):
        services.request_join(UserFactory(), space)
    closed = SpaceFactory(accepts_join_requests=False)
    with pytest.raises(services.JoinError):
        services.request_join(verified(UserFactory()), closed)


def test_only_admins_of_the_space_decide(client, setup_done):
    from allauth.mfa.models import Authenticator

    space = SpaceFactory()
    join = services.request_join(verified(UserFactory()), space)
    other_admin = MembershipFactory(role=Role.ADMIN).user
    Authenticator.objects.create(user=other_admin, type=Authenticator.Type.TOTP, data={})
    client.force_login(other_admin)
    other_space = other_admin.memberships.get().space
    response = client.post(
        reverse("core:members", args=[other_space.slug]),
        {"join_request": join.pk, "decision": "approve"},
    )
    assert response.status_code == 404
    join.refresh_from_db()
    assert join.status == JoinStatus.PENDING


def test_signup_follows_instance_setting(client, setup_done):
    url = reverse("account_signup")
    assert "account/signup_closed.html" in [t.name for t in client.get(url).templates]
    instance = InstanceSettings.load()
    instance.allow_signup = True
    instance.save()
    assert "account/signup_closed.html" not in [t.name for t in client.get(url).templates]
    assert url.encode() in client.get(reverse("account_login")).content


def test_export_contains_my_bookings(client, setup_done):
    m = MembershipFactory(role=Role.USER)
    resource = ResourceFactory(space=m.space)
    resource.area.open_weekdays = ALL_WEEKDAYS
    resource.area.save()
    start = (timezone.now() + dt.timedelta(days=3)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    booking.reserve(m.user, resource, start, start + dt.timedelta(hours=1), title="Secret run")
    client.force_login(m.user)
    data = json.loads(client.get(reverse("accounts:export")).content)
    assert data["profile"]["email"] == m.user.email
    assert data["bookings"][0]["title"] == "Secret run"
    assert data["memberships"][0]["space"] == m.space.name


def test_delete_account_anonymises_and_keeps_bookings(client, setup_done):
    m = MembershipFactory(role=Role.USER)
    resource = ResourceFactory(space=m.space)
    resource.area.open_weekdays = ALL_WEEKDAYS
    resource.area.save()
    start = (timezone.now() + dt.timedelta(days=3)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    b = booking.reserve(
        m.user, resource, start, start + dt.timedelta(hours=1), title="Private", note="notes"
    )
    client.force_login(m.user)
    assert client.post(reverse("accounts:delete"), {"password": "wrong"}).status_code == 302
    assert User.objects.get(pk=m.user.pk).is_active
    client.post(reverse("accounts:delete"), {"password": "correct horse battery"})
    user = User.objects.get(pk=m.user.pk)
    assert not user.is_active and user.username.startswith("deleted-") and user.first_name == ""
    assert not user.has_usable_password()
    assert not SpaceMembership.objects.filter(user=user).exists()
    b = Booking.objects.get(pk=b.pk)
    assert (b.title, b.note) == ("", "")
    assert not b.history.filter(title="Private").exists()
    # The session is gone: the next request is anonymous.
    assert client.get(reverse("core:home"))["Location"].startswith(reverse("account_login"))


def test_superuser_cannot_self_delete(client, setup_done):
    from allauth.mfa.models import Authenticator

    root = UserFactory(is_superuser=True, is_staff=True)
    Authenticator.objects.create(user=root, type=Authenticator.Type.TOTP, data={})
    client.force_login(root)
    client.post(reverse("accounts:delete"), {"password": "correct horse battery"})
    assert User.objects.get(pk=root.pk).is_active


def test_delete_account_detaches_client_accounts(setup_done):
    from apps.accounts.services import anonymize
    from apps.clients.tests.factories import ClientFactory

    m = MembershipFactory(role=Role.USER)
    lab = ClientFactory(space=m.space)
    lab.users.add(m.user)
    anonymize(m.user)
    assert not lab.users.exists()
