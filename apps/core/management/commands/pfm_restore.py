from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.core import backups


class Command(BaseCommand):
    help = "Restore the SQLite database from a snapshot (a safety backup is taken first)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("file", type=Path)
        parser.add_argument("--yes", action="store_true", help="Do not ask for confirmation.")

    def handle(self, *args: Any, **options: Any) -> None:
        source: Path = options["file"]
        if not source.is_file():
            raise CommandError(f"{source} does not exist")
        if not options["yes"]:
            answer = input(f"Replace the current database with {source}? [y/N] ")
            if answer.strip().lower() not in {"y", "yes", "o", "oui"}:
                raise CommandError("Aborted")
        safety = backups.backup("pre-restore")
        self.stdout.write(f"Current database saved to {safety}")
        try:
            backups.restore(source)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Restored. Restart the container to be safe."))
