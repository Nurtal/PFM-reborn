"""Core business rules: first-run setup, spaces and memberships."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import InstanceSettings, Role, Space, SpaceMembership

logger = logging.getLogger(__name__)

SETUP_TOKEN_FILENAME = "setup_token"  # noqa: S105 - a file name

_setup_done_cache = False


class SetupAlreadyCompleted(Exception):
    pass


class LastAdminError(Exception):
    """Raised when an action would leave a space without any administrator."""


def is_setup_completed() -> bool:
    # Once true it stays true: cache in-process to avoid a query per request.
    global _setup_done_cache
    if not _setup_done_cache:
        _setup_done_cache = InstanceSettings.objects.filter(
            setup_completed_at__isnull=False
        ).exists()
    return _setup_done_cache


def reset_setup_cache() -> None:
    global _setup_done_cache
    _setup_done_cache = False


def setup_token() -> str:
    """One-time token protecting the first-run wizard, printed in the logs.

    Without it, anyone reaching a fresh instance first could claim it.
    """
    path = settings.DATA_DIR / SETUP_TOKEN_FILENAME
    if not path.exists():
        path.write_text(secrets.token_urlsafe(16))
        path.chmod(0o600)
    token = path.read_text().strip()
    return token


def log_setup_token() -> None:
    if not is_setup_completed():
        logger.warning(
            "First start: open %s/setup/ and enter the setup token: %s",
            settings.PFM_URL,
            setup_token(),
        )


def check_setup_token(candidate: str) -> bool:
    return secrets.compare_digest(candidate.strip(), setup_token())


@dataclass(frozen=True)
class SetupData:
    username: str
    email: str
    password: str
    space_name: str
    space_slug: str
    site_name: str = "PFM"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: str = "starttls"
    smtp_from_email: str = ""


@transaction.atomic
def complete_setup(data: SetupData) -> User:
    """Create the super-admin, the first space and instance settings, once."""
    instance = InstanceSettings.load()
    if instance.setup_completed_at is not None:
        raise SetupAlreadyCompleted
    admin = User.objects.create_superuser(
        username=data.username, email=data.email, password=data.password
    )
    space = Space.objects.create(name=data.space_name, slug=data.space_slug)
    SpaceMembership.objects.create(user=admin, space=space, role=Role.ADMIN)

    instance.site_name = data.site_name
    instance.smtp_host = data.smtp_host
    instance.smtp_port = data.smtp_port
    instance.smtp_username = data.smtp_username
    instance.set_smtp_password(data.smtp_password)
    instance.smtp_security = data.smtp_security
    instance.smtp_from_email = data.smtp_from_email
    instance.setup_completed_at = timezone.now()
    instance.save()

    token_file = settings.DATA_DIR / SETUP_TOKEN_FILENAME
    transaction.on_commit(lambda: token_file.unlink(missing_ok=True))
    return admin


def spaces_for(user: User) -> list[Space]:
    qs = Space.objects.filter(is_active=True)
    if not user.is_superuser:
        qs = qs.filter(memberships__user=user)
    return list(qs.order_by("name"))


def _admin_count(space: Space) -> int:
    return SpaceMembership.objects.filter(space=space, role=Role.ADMIN).count()


@transaction.atomic
def add_member(space: Space, user: User, role: Role) -> SpaceMembership:
    membership, created = SpaceMembership.objects.get_or_create(
        space=space, user=user, defaults={"role": role}
    )
    if not created and membership.role != role:
        return change_role(membership, role)
    return membership


@transaction.atomic
def change_role(membership: SpaceMembership, role: Role) -> SpaceMembership:
    if membership.role == Role.ADMIN and role != Role.ADMIN and _admin_count(membership.space) <= 1:
        raise LastAdminError
    membership.role = role
    membership.save(update_fields=["role"])
    return membership


@transaction.atomic
def remove_member(membership: SpaceMembership) -> None:
    if membership.role == Role.ADMIN and _admin_count(membership.space) <= 1:
        raise LastAdminError
    membership.delete()
