"""Helpdesk rules: tickets from e-mails, replies by e-mail."""

from __future__ import annotations

import contextlib
import imaplib
import logging
from collections.abc import Callable
from typing import Any

from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Space
from apps.helpdesk.models import Mailbox, Ticket, TicketMessage, TicketStatus
from apps.helpdesk.parsing import Incoming, parse

logger = logging.getLogger(__name__)
MAX_PER_RUN = 50


@transaction.atomic
def ingest(space: Space, mail: Incoming) -> Ticket | None:
    """Store an incoming message; returns its ticket (None if ignored)."""
    if not mail.sender:
        return None
    if (
        mail.message_id
        and TicketMessage.objects.filter(ticket__space=space, message_id=mail.message_id).exists()
    ):
        return None  # already imported (re-delivered)
    ticket = None
    if mail.ticket_number is not None:
        ticket = Ticket.objects.filter(space=space, number=mail.ticket_number).first()
    if ticket is None:
        if mail.automatic:
            return None  # do not open tickets for bounces and auto-replies
        number = (Ticket.objects.filter(space=space).aggregate(n=Max("number"))["n"] or 0) + 1
        ticket = Ticket.objects.create(
            space=space,
            number=number,
            subject=mail.subject,
            requester_email=mail.sender,
            requester=User.objects.filter(email__iexact=mail.sender, is_active=True).first(),
        )
    elif ticket.status != TicketStatus.OPEN:
        ticket.status = TicketStatus.OPEN  # the requester answered
        ticket.save(update_fields=["status", "updated_at"])
    TicketMessage.objects.create(
        ticket=ticket, incoming=True, sender=mail.sender, body=mail.body, message_id=mail.message_id
    )
    return ticket


def fetch(mailbox: Mailbox, connect: Callable[..., Any] | None = None) -> int:
    """Import unread messages of ``mailbox``; returns how many were stored."""
    if connect is None:
        connect = imaplib.IMAP4_SSL if mailbox.imap_ssl else imaplib.IMAP4
    stored = 0
    try:
        imap = connect(mailbox.imap_host, mailbox.imap_port, timeout=20)
        try:
            if not mailbox.imap_ssl:
                imap.starttls()
            imap.login(mailbox.username, mailbox.get_password())
            imap.select(mailbox.folder)
            _typ, data = imap.search(None, "UNSEEN")
            for num in (data[0] or b"").decode().split()[:MAX_PER_RUN]:
                _typ, parts = imap.fetch(num, "(RFC822)")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")
                if ingest(mailbox.space, parse(raw)) is not None:
                    stored += 1
                imap.store(num, "+FLAGS", "\\Seen")
        finally:
            with contextlib.suppress(OSError, imaplib.IMAP4.error):
                imap.logout()
        mailbox.last_error = ""
    except (OSError, imaplib.IMAP4.error) as exc:
        logger.warning("Helpdesk %s: %s", mailbox.address, exc)
        mailbox.last_error = str(exc)[:500]
    mailbox.last_checked_at = timezone.now()
    mailbox.save(update_fields=["last_checked_at", "last_error"])
    return stored


def reply(ticket: Ticket, author: User, body: str, close: bool = False) -> TicketMessage:
    mailbox = Mailbox.objects.filter(space=ticket.space).first()
    from_address = mailbox.address if mailbox else None
    subject = f"[#{ticket.number}] {ticket.subject}"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    message = TicketMessage.objects.create(
        ticket=ticket, incoming=False, sender=from_address or "", author=author, body=body
    )
    ticket.status = TicketStatus.CLOSED if close else TicketStatus.WAITING
    ticket.save(update_fields=["status", "updated_at"])
    email = EmailMessage(
        subject,
        body,
        from_address,
        [ticket.requester_email],
        reply_to=[from_address] if from_address else None,
        headers={"Auto-Submitted": "no"},
    )
    transaction.on_commit(lambda: email.send(fail_silently=False))
    return message
