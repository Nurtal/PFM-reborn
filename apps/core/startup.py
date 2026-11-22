"""Container start-up preparation: pre-migration backup, migrations, setup token.

In the container it runs inside the web process (``config.wsgi``), so Django
is loaded once per start instead of twice (``manage.py pfm_init`` then the
web server): about 2 s less downtime on every start and update. A file lock
and a per-start marker make it run once even with several web workers or
respawned workers. ``manage.py pfm_init`` runs the same steps by hand.
"""

from __future__ import annotations

import fcntl
import logging
from pathlib import Path

logger = logging.getLogger("pfm.startup")

MARKER = "pfm-prepared"


def prepare() -> None:
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from huey.contrib.djhuey import HUEY

    from apps.core import backups, services

    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    has_tables = bool(connection.introspection.table_names())
    if plan and has_tables and backups.is_sqlite():
        path = backups.backup("pre-migrate")
        logger.warning("Pending migrations: database backed up to %s", path)
    if plan:  # nothing to do on most starts: skip the second migrate pass
        call_command("migrate", interactive=False, verbosity=1)
    # Locks left by a crash would block periodic tasks forever: clear them
    # before any task consumer starts.
    flushed = HUEY.flush_locks()
    if flushed:
        logger.warning("Cleared stale task locks: %s", ", ".join(sorted(flushed)))
    services.log_setup_token()


def prepare_once(runtime_dir: Path) -> bool:
    """Run :func:`prepare` once per container start; True if this call ran it.

    The entrypoint deletes the marker before starting the server, so a
    restarted container prepares again (stale locks, new image).
    """
    runtime_dir.mkdir(parents=True, exist_ok=True)
    marker = runtime_dir / MARKER
    with (runtime_dir / f"{MARKER}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if marker.exists():
            return False
        prepare()
        marker.touch()
    return True
