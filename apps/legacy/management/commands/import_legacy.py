"""python manage.py import_legacy --source mysql://user:pass@host/pfm --space all [--dry-run]"""

from __future__ import annotations

import json
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone

from apps.legacy.importer import Importer
from apps.legacy.sources import open_source


class _DryRun(Exception):
    pass


class Command(BaseCommand):
    help = "Import data from the old Platform-Manager database (read-only, re-runnable)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--source", "--mysql-url", dest="source", required=True)
        parser.add_argument(
            "--space", default="all", help="Legacy space id(s), comma separated, or 'all'."
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Run everything, then roll back."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from apps.accounts.models import User

        if not User.objects.filter(is_superuser=True).exists():
            raise CommandError("Complete the first-run setup (an administrator is needed) first.")
        raw = options["space"]
        try:
            space_ids = None if raw == "all" else {int(x) for x in raw.split(",") if x.strip()}
        except ValueError as exc:
            raise CommandError("--space takes ids such as 3 or 3,5, or 'all'") from exc
        try:
            source = open_source(options["source"])
        except (ValueError, RuntimeError) as exc:
            raise CommandError(str(exc)) from exc

        importer = Importer(source, space_ids)
        try:
            with transaction.atomic():
                report = importer.run()
                if options["dry_run"]:
                    raise _DryRun
        except _DryRun:
            self.stdout.write(self.style.WARNING("Dry run: nothing was saved."))
        data = report.as_dict()
        stamp = timezone.now().strftime("%Y%m%dT%H%M%S")
        path = (
            settings.DATA_DIR / f"legacy-import-{stamp}{'-dry' if options['dry_run'] else ''}.json"
        )
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        for table in sorted(set(data["created"]) | set(data["updated"]) | set(data["skipped"])):
            counts = {k: data[k].get(table, 0) for k in ("created", "updated", "skipped")}
            self.stdout.write(
                f"{table:22} created {counts['created']:6}  updated {counts['updated']:6}  "
                f"skipped {counts['skipped']:6}"
            )
        self.stdout.write(f"{len(data['anomalies'])} anomalies. Full report: {path}")
