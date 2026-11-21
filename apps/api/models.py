from __future__ import annotations

import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

PREFIX = "pfm_"


def digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class ApiToken(models.Model):
    """Personal API token. Only its SHA-256 is stored; the key is shown once."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_tokens"
    )
    name = models.CharField(_("name"), max_length=100)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    hint = models.CharField(max_length=12, editable=False)  # first characters, to recognise it
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(_("expires"), null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("API token")
        verbose_name_plural = _("API tokens")

    def __str__(self) -> str:
        return f"{self.name} ({self.hint}…)"

    @classmethod
    def issue(cls, user: object, name: str, expires_at: object = None) -> tuple[ApiToken, str]:
        key = PREFIX + secrets.token_urlsafe(32)
        token = cls.objects.create(
            user=user, name=name, key_hash=digest(key), hint=key[:10], expires_at=expires_at
        )
        return token, key

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()
