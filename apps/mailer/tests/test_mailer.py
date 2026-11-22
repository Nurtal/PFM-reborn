import datetime as dt

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.booking import services as booking
from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory, SpaceFactory
from apps.mailer import services
from apps.mailer.models import Audience, Mailing
from apps.resources.models import ALL_WEEKDAYS
from apps.resources.tests.factories import ResourceFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def space():
    s = SpaceFactory()
    for role in (Role.VISITOR, Role.USER, Role.MANAGER, Role.ADMIN):
        MembershipFactory(space=s, role=role)
    MembershipFactory()  # member of another space
    return s


@pytest.mark.parametrize(
    ("audience", "count"), [(Audience.ALL, 4), (Audience.USERS, 3), (Audience.MANAGERS, 2)]
)
def test_audiences(space, audience, count):
    mailing = Mailing(space=space, subject="s", body="b", audience=audience)
    assert len(services.recipients(mailing)) == count


def test_resource_audience(space):
    resource = ResourceFactory(space=space)
    resource.area.open_weekdays = ALL_WEEKDAYS
    resource.area.save()
    user = space.memberships.get(role=Role.USER).user
    start = (timezone.now() + dt.timedelta(days=2)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    booking.reserve(user, resource, start, start + dt.timedelta(hours=1))
    mailing = Mailing(
        space=space, subject="s", body="b", audience=Audience.RESOURCE, resource=resource
    )
    assert services.recipients(mailing) == [user.email]


def test_each_recipient_gets_a_separate_message(space, django_capture_on_commit_callbacks):
    mailing = Mailing.objects.create(
        space=space, subject="Closure", body="Closed Friday", audience=Audience.ALL
    )
    with django_capture_on_commit_callbacks(execute=True):
        services.send(mailing)
    assert len(mail.outbox) == 4
    assert all(len(m.to) == 1 and not m.cc and not m.bcc for m in mail.outbox)
    mailing.refresh_from_db()
    assert mailing.sent_at is not None and mailing.recipients_count == 4


def test_two_step_form(client, space, setup_done, django_capture_on_commit_callbacks):
    manager = space.memberships.get(role=Role.MANAGER).user
    client.force_login(manager)
    url = reverse("mailer:index", args=[space.slug])
    data = {"audience": "all", "subject": "Hello", "body": "World"}
    response = client.post(url, data)
    assert response.context["preview"] == 4 and not Mailing.objects.exists()
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(url, {**data, "confirm": "1"})
    assert response.status_code == 302 and len(mail.outbox) == 4


def test_subject_header_injection_is_refused(client, space, setup_done):
    client.force_login(space.memberships.get(role=Role.MANAGER).user)
    response = client.post(
        reverse("mailer:index", args=[space.slug]),
        {"audience": "all", "subject": "Hi\nBcc: victim@example.org", "body": "x", "confirm": "1"},
    )
    assert "subject" in response.context["form"].errors


def test_mailer_is_for_managers(client, space, setup_done):
    client.force_login(space.memberships.get(role=Role.USER).user)
    assert client.get(reverse("mailer:index", args=[space.slug])).status_code == 404


def test_role_in_another_space_does_not_count(space):
    visitor = space.memberships.get(role=Role.VISITOR).user
    MembershipFactory(user=visitor, role=Role.MANAGER)  # manager elsewhere
    mailing = Mailing(space=space, subject="s", body="b", audience=Audience.MANAGERS)
    assert visitor.email not in services.recipients(mailing)
