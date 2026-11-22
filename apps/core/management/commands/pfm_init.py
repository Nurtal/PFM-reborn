"""Container start-up: back up the database if migrations are pending, then migrate."""

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.core import backups, services


class Command(BaseCommand):
    help = "Prepare /data: pre-migration backup, migrations, setup token."

    def handle(self, *args: object, **options: object) -> None:
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        has_tables = bool(connection.introspection.table_names())
        if plan and has_tables and backups.is_sqlite():
            path = backups.backup("pre-migrate")
            self.stdout.write(f"Pending migrations: database backed up to {path}")
        call_command("migrate", interactive=False, verbosity=1)
        services.log_setup_token()
