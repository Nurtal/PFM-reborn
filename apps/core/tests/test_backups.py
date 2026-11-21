import os
import sqlite3
import time

import pytest

from apps.core import backups


@pytest.fixture
def live_db(tmp_path, settings, monkeypatch):
    path = tmp_path / "live.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('before')")
    settings.BACKUP_DIR = tmp_path / "backups"
    settings.BACKUP_DIR.mkdir()
    monkeypatch.setattr(backups, "database_path", lambda: path)
    monkeypatch.setattr(backups, "is_sqlite", lambda: True)
    return path


def _values(path):
    with sqlite3.connect(path) as conn:
        return [r[0] for r in conn.execute("SELECT v FROM t")]


def test_backup_then_restore_roundtrip(live_db):
    snapshot = backups.backup("test")
    with sqlite3.connect(live_db) as conn:
        conn.execute("UPDATE t SET v = 'after'")
    assert _values(live_db) == ["after"]

    backups.restore(snapshot)
    assert _values(live_db) == ["before"]


def test_prune_keeps_recent_backups(live_db, settings):
    recent = backups.backup("recent")
    old = backups.backup("old")
    ancient = time.time() - 30 * 86400
    os.utime(old, (ancient, ancient))
    removed = backups.prune(retention_days=14)
    assert removed == [old]
    assert recent.exists()
