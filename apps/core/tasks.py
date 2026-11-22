import logging

from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from apps.core import backups

logger = logging.getLogger(__name__)


@db_periodic_task(crontab(hour="3", minute="15"))
def nightly_backup() -> None:
    if not backups.is_sqlite():
        return
    path = backups.backup("nightly")
    removed = backups.prune()
    logger.info("Nightly backup written to %s, %d old backup(s) pruned", path, len(removed))
