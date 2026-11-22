import threading
from pathlib import Path

import pytest

from apps.core import startup


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    seen: list[int] = []

    def fake_prepare() -> None:
        seen.append(1)

    monkeypatch.setattr(startup, "prepare", fake_prepare)
    return seen


def test_prepares_once_per_start(tmp_path: Path, calls: list[int]) -> None:
    assert startup.prepare_once(tmp_path) is True
    assert startup.prepare_once(tmp_path) is False  # second worker, respawn
    assert calls == [1]
    (tmp_path / startup.MARKER).unlink()  # what the entrypoint does on start
    assert startup.prepare_once(tmp_path) is True
    assert calls == [1, 1]


def test_concurrent_workers_prepare_once(tmp_path: Path, calls: list[int]) -> None:
    threads = [threading.Thread(target=startup.prepare_once, args=(tmp_path,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == [1]


def test_failure_leaves_no_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> None:
        raise RuntimeError("migration failed")

    monkeypatch.setattr(startup, "prepare", broken)
    with pytest.raises(RuntimeError):
        startup.prepare_once(tmp_path)
    assert not (tmp_path / startup.MARKER).exists()


@pytest.mark.django_db
def test_prepare_on_migrated_database_is_a_no_op() -> None:
    startup.prepare()  # nothing pending: no backup, no migrate, no error
