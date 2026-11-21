from __future__ import annotations

import zoneinfo
from typing import TYPE_CHECKING, Any, Self

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

if TYPE_CHECKING:
    from apps.accounts.models import User

hex_color = RegexValidator(r"^#[0-9a-fA-F]{6}$", _("Use a hex colour such as #4f46e5."))


def validate_timezone(value: str) -> None:
    # Validator rather than choices: the tz list varies between systems and
    # would make migrations churn.
    if value not in zoneinfo.available_timezones():
        raise ValidationError(_("Unknown time zone."))


class Role(models.IntegerChoices):
    """Ordered roles: a higher value includes every right of the lower ones."""

    VISITOR = 10, _("Visitor")
    USER = 20, _("User")
    MANAGER = 30, _("Manager")
    ADMIN = 40, _("Space administrator")


class SMTPSecurity(models.TextChoices):
    STARTTLS = "starttls", "STARTTLS"
    SSL = "ssl", "SSL/TLS"
    NONE = "none", _("None")


class Space(models.Model):
    """A technology platform (imaging, cytometry...). Business data hangs off it."""

    name = models.CharField(_("name"), max_length=120)
    slug = models.SlugField(_("short name"), max_length=50, unique=True)
    description = models.TextField(_("description"), blank=True)
    timezone = models.CharField(
        _("time zone"), max_length=64, default="Europe/Paris", validators=[validate_timezone]
    )
    currency = models.CharField(_("currency"), max_length=3, default="EUR")
    accent_color = models.CharField(
        _("accent colour"), max_length=7, default="#4f46e5", validators=[hex_color]
    )
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]
        verbose_name = _("space")
        verbose_name_plural = _("spaces")

    def __str__(self) -> str:
        return self.name


class SpaceMembership(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="memberships")
    role = models.PositiveSmallIntegerField(_("role"), choices=Role.choices, default=Role.USER)
    created_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "space"], name="unique_membership"),
        ]
        ordering = ["space__name", "user__username"]
        verbose_name = _("membership")
        verbose_name_plural = _("memberships")

    def __str__(self) -> str:
        return f"{self.user} @ {self.space} ({self.get_role_display()})"


class SpaceQuerySet(models.QuerySet):
    def for_space(self, space: Space) -> Self:
        return self.filter(space=space)


class SpaceScopedModel(models.Model):
    """Base for every business model. Always query through ``objects.for_space()``."""

    space = models.ForeignKey(Space, on_delete=models.PROTECT, related_name="+")

    objects = SpaceQuerySet.as_manager()

    class Meta:
        abstract = True


class InstanceSettings(models.Model):
    """Singleton holding instance-wide settings editable from the UI."""

    site_name = models.CharField(_("site name"), max_length=120, default="PFM")
    setup_completed_at = models.DateTimeField(null=True, blank=True)

    smtp_host = models.CharField(_("SMTP host"), max_length=255, blank=True)
    smtp_port = models.PositiveIntegerField(_("SMTP port"), default=587)
    smtp_username = models.CharField(_("SMTP user"), max_length=255, blank=True)
    smtp_password_encrypted = models.TextField(blank=True, editable=False)
    smtp_security = models.CharField(
        _("security"),
        max_length=8,
        choices=SMTPSecurity.choices,
        default=SMTPSecurity.STARTTLS,
    )
    smtp_from_email = models.EmailField(_("sender address"), blank=True)

    history = HistoricalRecords(excluded_fields=["smtp_password_encrypted"])

    class Meta:
        verbose_name = _("instance settings")
        verbose_name_plural = _("instance settings")

    def __str__(self) -> str:
        return self.site_name

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> InstanceSettings:
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def set_smtp_password(self, raw: str) -> None:
        from apps.core.crypto import encrypt

        self.smtp_password_encrypted = encrypt(raw) if raw else ""

    def get_smtp_password(self) -> str:
        from apps.core.crypto import decrypt

        return decrypt(self.smtp_password_encrypted) if self.smtp_password_encrypted else ""


def user_role(user: User, space: Space) -> int | None:
    """Role of ``user`` in ``space`` (cached on the user object for the request)."""
    # request.user is a SimpleLazyObject while permission backends get the real
    # user: cache on the real object so both paths share it.
    target = getattr(user, "_wrapped", user)
    cache: dict[int, int | None] = target.__dict__.setdefault("_pfm_roles", {})
    if space.pk not in cache:
        cache[space.pk] = (
            SpaceMembership.objects.filter(user=user, space=space)
            .values_list("role", flat=True)
            .first()
        )
    return cache[space.pk]
