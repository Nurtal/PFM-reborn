from django.core.management.base import BaseCommand

from apps.core import backups


class Command(BaseCommand):
    help = "Write a consistent snapshot of the SQLite database into /data/backups."

    def handle(self, *args: object, **options: object) -> None:
        path = backups.backup("manual")
        self.stdout.write(self.style.SUCCESS(str(path)))
