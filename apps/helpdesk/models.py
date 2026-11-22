from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import Space, SpaceScopedModel


class Mailbox(models.Model):
    """IMAP mailbox feeding a space's helpdesk (one per space)."""

    space = models.OneToOneField(Space, on_delete=models.CASCADE, related_name="mailbox")
    address = models.EmailField(_("support address"))
    imap_host = models.CharField(_("IMAP host"), max_length=255)
    imap_port = models.PositiveIntegerField(_("IMAP port"), default=993)
    imap_ssl = models.BooleanField(_("SSL/TLS"), default=True)
    username = models.CharField(_("IMAP user"), max_length=255)
    password_encrypted = models.TextField(blank=True, editable=False)
    folder = models.CharField(_("folder"), max_length=100, default="INBOX")
    is_active = models.BooleanField(_("active"), default=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)

    class Meta:
        verbose_name = _("helpdesk mailbox")
        verbose_name_plural = _("helpdesk mailboxes")

    def __str__(self) -> str:
        return self.address

    def set_password(self, raw: str) -> None:
        from apps.core.crypto import encrypt

        self.password_encrypted = encrypt(raw) if raw else ""

    def get_password(self) -> str:
        from apps.core.crypto import decrypt

        return decrypt(self.password_encrypted) if self.password_encrypted else ""


class TicketStatus(models.TextChoices):
    OPEN = "open", _("Open")
    WAITING = "waiting", _("Waiting for the requester")
    CLOSED = "closed", _("Closed")


class Ticket(SpaceScopedModel):
    number = models.PositiveIntegerField()
    subject = models.CharField(_("subject"), max_length=300)
    requester_email = models.EmailField(_("requester"))
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("assigned to"),
    )
    status = models.CharField(
        _("status"), max_length=10, choices=TicketStatus.choices, default=TicketStatus.OPEN
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = _("ticket")
        verbose_name_plural = _("tickets")
        constraints = [
            models.UniqueConstraint(fields=["space", "number"], name="unique_ticket_number"),
        ]

    def __str__(self) -> str:
        return f"#{self.number} {self.subject}"


class TicketMessage(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    incoming = models.BooleanField(default=True)
    sender = models.CharField(max_length=320)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    body = models.TextField()
    message_id = models.CharField(max_length=998, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"{self.sender}: {self.body[:40]}"
