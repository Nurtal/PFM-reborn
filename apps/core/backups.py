"""Consistent SQLite snapshots in ``/data/backups`` (README §11)."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

PREFIX = "db-"
SUFFIX = ".sqlite3"


def is_sqlite() -> bool:
    return connection.vendor == "sqlite"


def database_path() -> Path:
    return Path(settings.DATABASES["default"]["NAME"])


def backup(label: str = "") -> Path:
    """Write a consistent copy of the live database using SQLite's online backup API."""
    if not is_sqlite():
        raise RuntimeError("Built-in backups only cover SQLite; use pg_dump for PostgreSQL.")
    stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
    name = f"{PREFIX}{stamp}{'-' + label if label else ''}{SUFFIX}"
    target = Path(settings.BACKUP_DIR) / name
    src = sqlite3.connect(database_path())
    dst = sqlite3.connect(target)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    target.chmod(0o600)
    return target


def restore(source: Path) -> None:
    """Copy a snapshot back into the live database (atomically, page by page)."""
    if not is_sqlite():
        raise RuntimeError("Built-in restore only covers SQLite.")
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError(f"{source} failed the SQLite integrity check")
        connection.close()
        dst = sqlite3.connect(database_path())
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def prune(retention_days: int | None = None) -> list[Path]:
    days = settings.PFM_BACKUP_RETENTION_DAYS if retention_days is None else retention_days
    cutoff = (timezone.now() - timedelta(days=days)).timestamp()
    removed = []
    for path in Path(settings.BACKUP_DIR).glob(f"{PREFIX}*{SUFFIX}"):
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed.append(path)
    return removed
