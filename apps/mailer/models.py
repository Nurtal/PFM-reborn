from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import Role, SpaceScopedModel


class Audience(models.TextChoices):
    ALL = "all", _("Every member")
    USERS = "users", _("Users and above (not visitors)")
    MANAGERS = "managers", _("Managers and administrators")
    RESOURCE = "resource", _("Users who booked a resource recently")


AUDIENCE_MIN_ROLE = {
    Audience.ALL: Role.VISITOR,
    Audience.USERS: Role.USER,
    Audience.MANAGERS: Role.MANAGER,
}


class Mailing(SpaceScopedModel):
    """A message sent to members of a platform; kept as a history."""

    subject = models.CharField(_("subject"), max_length=200)
    body = models.TextField(_("message"))
    audience = models.CharField(_("recipients"), max_length=10, choices=Audience.choices)
    resource = models.ForeignKey(
        "resources.Resource", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    recipients_count = models.PositiveIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("mailing")
        verbose_name_plural = _("mailings")

    def __str__(self) -> str:
        return self.subject
