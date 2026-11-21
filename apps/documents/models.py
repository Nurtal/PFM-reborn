from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import SpaceScopedModel


class Visibility(models.TextChoices):
    MEMBERS = "members", _("Every member of the platform")
    MANAGERS = "managers", _("Managers only")


def _path(instance: Document, filename: str) -> str:
    # The name was already randomised by core.uploads; never trust the client's.
    return f"documents/{instance.space_id}/{filename}"


class Document(SpaceScopedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    title = models.CharField(_("title"), max_length=200)
    description = models.TextField(_("description"), blank=True)
    file = models.FileField(upload_to=_path)
    original_name = models.CharField(max_length=200)
    content_type = models.CharField(max_length=120)
    size = models.PositiveBigIntegerField()
    visibility = models.CharField(
        _("visible to"), max_length=10, choices=Visibility.choices, default=Visibility.MEMBERS
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("document")
        verbose_name_plural = _("documents")

    def __str__(self) -> str:
        return self.title
