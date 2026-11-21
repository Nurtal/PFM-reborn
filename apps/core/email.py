"""Email backend reading SMTP settings saved through the admin UI."""

from __future__ import annotations

from typing import Any

from django.core.mail.backends.console import EmailBackend as ConsoleBackend
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend

from apps.core.models import InstanceSettings


def smtp_backend_from(instance: InstanceSettings, **kwargs: Any) -> SMTPBackend:
    return SMTPBackend(
        host=instance.smtp_host,
        port=instance.smtp_port,
        username=instance.smtp_username or None,
        password=instance.get_smtp_password() or None,
        use_tls=instance.smtp_security == "starttls",
        use_ssl=instance.smtp_security == "ssl",
        timeout=10,
        **kwargs,
    )


class InstanceSMTPBackend:
    """Delegates to SMTP when configured, otherwise logs mails to the console."""

    def __new__(cls, **kwargs: Any) -> Any:  # type: ignore[misc]
        instance = InstanceSettings.load()
        if instance.smtp_host:
            return smtp_backend_from(instance, **kwargs)
        return ConsoleBackend(**kwargs)
