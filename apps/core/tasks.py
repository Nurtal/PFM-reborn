import logging

from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task, lock_task

from apps.core import backups

logger = logging.getLogger(__name__)


@db_periodic_task(crontab(hour="3", minute="15"))
@lock_task("nightly-backup")  # one run even with several web workers
def nightly_backup() -> None:
    if not backups.is_sqlite():
        return
    path = backups.backup("nightly")
    removed = backups.prune()
    logger.info("Nightly backup written to %s, %d old backup(s) pruned", path, len(removed))


@db_task(retries=2, retry_delay=60)
def notify_join_request(join_id: int) -> None:
    from django.conf import settings
    from django.core.mail import send_mail
    from django.utils import translation
    from django.utils.translation import gettext as _

    from apps.core.models import JoinRequest, Role

    join = JoinRequest.objects.select_related("user", "space").filter(pk=join_id).first()
    if join is None:
        return
    admins = list(
        join.space.memberships.filter(role=Role.ADMIN, user__is_active=True)
        .exclude(user__email="")
        .values_list("user__email", flat=True)
    )
    if not admins:
        return
    with translation.override(settings.LANGUAGE_CODE):
        subject = _("[%(space)s] Access request from %(user)s") % {
            "space": join.space.name,
            "user": join.user,
        }
        body = _("%(user)s (%(email)s) asks to join %(space)s.") % {
            "user": join.user,
            "email": join.user.email,
            "space": join.space.name,
        }
        body += f"\n\n{join.message}\n\n{settings.PFM_URL}/s/{join.space.slug}/members/"
    send_mail(subject, body, None, admins)


@db_task(retries=2, retry_delay=60)
def notify_join_decision(join_id: int) -> None:
    from django.conf import settings
    from django.core.mail import send_mail
    from django.utils import translation
    from django.utils.translation import gettext as _

    from apps.core.models import JoinRequest, JoinStatus

    join = JoinRequest.objects.select_related("user", "space").filter(pk=join_id).first()
    if join is None or not join.user.email:
        return
    with translation.override(settings.LANGUAGE_CODE):
        if join.status == JoinStatus.APPROVED:
            body = _("Your access to %(space)s has been approved.") % {"space": join.space.name}
            body += f"\n\n{settings.PFM_URL}/s/{join.space.slug}/"
        else:
            body = _("Your access request to %(space)s was declined.") % {"space": join.space.name}
    send_mail(f"[{join.space.name}] {_('Access request')}", body, None, [join.user.email])
