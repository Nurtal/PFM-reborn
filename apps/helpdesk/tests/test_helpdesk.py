from email.message import EmailMessage

import pytest
from django.core import mail
from django.urls import reverse

from apps.core.models import Role
from apps.core.tests.factories import MembershipFactory, SpaceFactory
from apps.helpdesk import services
from apps.helpdesk.models import Mailbox, Ticket, TicketStatus
from apps.helpdesk.parsing import parse

pytestmark = pytest.mark.django_db


def raw(
    subject="Microscope down",
    sender="Alice <alice@lab.fr>",
    body="It does not start.",
    html=None,
    headers=None,
    msg_id="<1@lab.fr>",
):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Message-ID"] = (
        sender,
        "support@pfm.fr",
        subject,
        msg_id,
    )
    for k, v in (headers or {}).items():
        msg[k] = v
    if html:
        msg.set_content(body)
        msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(body)
    return msg.as_bytes()


def test_parse_plain_and_tag():
    m = parse(raw(subject="Re: [#12] Microscope down"))
    assert (m.sender, m.ticket_number, m.automatic) == ("alice@lab.fr", 12, False)
    assert m.body == "It does not start."


def test_parse_html_only_strips_markup_and_scripts():
    msg = EmailMessage()
    msg["From"], msg["Subject"] = "bob@lab.fr", "Hi"
    msg.set_content("<p>Hello</p><script>alert(1)</script><p>World</p>", subtype="html")
    m = parse(msg.as_bytes())
    assert "alert" not in m.body and "Hello" in m.body and "<p>" not in m.body


@pytest.mark.parametrize(
    "headers",
    [{"Auto-Submitted": "auto-replied"}, {"Precedence": "bulk"}, {"List-Id": "<x.lab.fr>"}],
)
def test_automatic_messages_do_not_open_tickets(headers):
    space = SpaceFactory()
    assert services.ingest(space, parse(raw(headers=headers))) is None
    assert not Ticket.objects.exists()


def test_new_ticket_then_threaded_reply():
    space = SpaceFactory()
    ticket = services.ingest(space, parse(raw()))
    assert ticket.number == 1 and ticket.messages.count() == 1
    again = services.ingest(
        space, parse(raw(subject=f"Re: [#{ticket.number}] Microscope down", msg_id="<2@lab.fr>"))
    )
    assert again == ticket and ticket.messages.count() == 2
    assert services.ingest(space, parse(raw(msg_id="<2@lab.fr>"))) is None  # duplicate delivery
    other = SpaceFactory()
    assert (
        services.ingest(other, parse(raw(subject="[#1] x", msg_id="<3@lab.fr>"))).space == other
    )  # numbers are per space


def test_reply_sends_tagged_mail_and_waits(django_capture_on_commit_callbacks):
    space = SpaceFactory()
    Mailbox.objects.create(space=space, address="support@pfm.fr", imap_host="imap", username="u")
    manager = MembershipFactory(space=space, role=Role.MANAGER).user
    ticket = services.ingest(space, parse(raw()))
    with django_capture_on_commit_callbacks(execute=True):
        services.reply(ticket, manager, "Fixed, try again.")
    sent = mail.outbox[-1]
    assert sent.subject == "Re: [#1] Microscope down"
    assert sent.to == ["alice@lab.fr"] and sent.from_email == "support@pfm.fr"
    ticket.refresh_from_db()
    assert ticket.status == TicketStatus.WAITING
    services.ingest(space, parse(raw(subject="Re: [#1] Microscope down", msg_id="<9@lab.fr>")))
    ticket.refresh_from_db()
    assert ticket.status == TicketStatus.OPEN  # requester answered


class FakeIMAP:
    def __init__(self, host, port, timeout=None):
        self.messages = {"1": raw(), "2": raw(subject="Other", msg_id="<5@lab.fr>")}
        self.seen = []

    def login(self, user, password):
        assert password == "pw"

    def select(self, folder):
        pass

    def search(self, charset, criterion):
        return "OK", [" ".join(self.messages).encode()]

    def fetch(self, num, what):
        return "OK", [(b"1 (RFC822 {n}", self.messages[num]), b")"]

    def store(self, num, op, flag):
        self.seen.append(num)

    def logout(self):
        pass


def test_fetch_imports_unseen_messages():
    space = SpaceFactory()
    mailbox = Mailbox.objects.create(
        space=space, address="s@pfm.fr", imap_host="imap", username="u"
    )
    mailbox.set_password("pw")
    mailbox.save()
    assert services.fetch(mailbox, connect=FakeIMAP) == 2
    assert Ticket.objects.filter(space=space).count() == 2
    mailbox.refresh_from_db()
    assert mailbox.last_error == "" and mailbox.last_checked_at


def test_fetch_records_connection_errors():
    space = SpaceFactory()
    mailbox = Mailbox.objects.create(
        space=space, address="s@pfm.fr", imap_host="imap", username="u"
    )

    def broken(*args, **kwargs):
        raise OSError("connection refused")

    assert services.fetch(mailbox, connect=broken) == 0
    mailbox.refresh_from_db()
    assert "refused" in mailbox.last_error


def test_pages_permissions(client, setup_done):
    space = SpaceFactory()
    services.ingest(space, parse(raw()))
    client.force_login(MembershipFactory(space=space, role=Role.USER).user)
    assert client.get(reverse("helpdesk:list", args=[space.slug])).status_code == 404
    manager = MembershipFactory(space=space, role=Role.MANAGER).user
    client.force_login(manager)
    assert client.get(reverse("helpdesk:detail", args=[space.slug, 1])).status_code == 200
    assert (
        client.get(reverse("helpdesk:mailbox", args=[space.slug])).status_code == 404
    )  # admins only
