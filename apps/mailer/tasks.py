import logging

from django.core.mail import EmailMessage, get_connection
from django.utils import timezone
from huey.contrib.djhuey import db_task

logger = logging.getLogger(__name__)


@db_task(retries=2, retry_delay=120)
def deliver(mailing_id: int) -> None:
    from apps.mailer.models import Mailing
    from apps.mailer.services import recipients

    mailing = Mailing.objects.select_related("space", "sent_by").filter(pk=mailing_id).first()
    if mailing is None or mailing.sent_at is not None:
        return
    reply_to = [mailing.sent_by.email] if mailing.sent_by and mailing.sent_by.email else []
    subject = f"[{mailing.space.name}] {mailing.subject}"
    with get_connection() as connection:
        messages = [
            EmailMessage(
                subject, mailing.body, None, [address], reply_to=reply_to, connection=connection
            )
            for address in recipients(mailing)
        ]
        sent = connection.send_messages(messages) or 0
    mailing.sent_at = timezone.now()
    mailing.save(update_fields=["sent_at"])
    logger.info("Mailing %s delivered to %s recipients", mailing.pk, sent)
