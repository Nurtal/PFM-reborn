"""Back up the database if migrations are pending, migrate, clear stale task locks.

The container runs these steps inside the web process (see apps.core.startup);
this command is for manual use and for PFM_TASKS=separate.
"""

from django.core.management.base import BaseCommand

from apps.core import startup


class Command(BaseCommand):
    help = "Prepare /data: pre-migration backup, migrations, setup token."

    def handle(self, *args: object, **options: object) -> None:
        startup.prepare()
