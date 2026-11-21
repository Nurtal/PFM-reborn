from huey import crontab
from huey.contrib.djhuey import db_periodic_task, lock_task


@db_periodic_task(crontab(minute="*/5"))
@lock_task("helpdesk-fetch")
def fetch_mailboxes() -> None:
    from apps.helpdesk.models import Mailbox
    from apps.helpdesk.services import fetch

    for mailbox in Mailbox.objects.filter(is_active=True, space__is_active=True).select_related(
        "space"
    ):
        fetch(mailbox)
