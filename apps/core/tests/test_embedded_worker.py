import time

import pytest
from huey.contrib.djhuey import HUEY

from apps.core import embedded_worker


@pytest.fixture
def real_queue(monkeypatch):
    monkeypatch.setattr(HUEY, "immediate", False)
    monkeypatch.setattr(embedded_worker, "_started", type(embedded_worker._started)())
    yield HUEY
    if embedded_worker._consumer is not None:
        embedded_worker._consumer.stop(graceful=True)
        embedded_worker._consumer = None


@HUEY.task()
def add(a, b):
    return a + b


def test_embedded_consumer_runs_queued_tasks(real_queue):
    consumer = embedded_worker.start(workers=1)
    assert consumer is not None
    assert embedded_worker.start() is None  # once per process
    result = add(2, 3)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = result.get()
        if value is not None:
            break
        time.sleep(0.05)
    assert value == 5


def test_app_tasks_are_registered(real_queue):
    embedded_worker.start(workers=1)
    names = set(HUEY._registry._registry)
    assert {"apps.core.tasks.nightly_backup", "apps.helpdesk.tasks.fetch_mailboxes"} <= names


def test_not_started_in_immediate_mode(monkeypatch):
    monkeypatch.setattr(embedded_worker, "_started", type(embedded_worker._started)())
    assert HUEY.immediate
    assert embedded_worker.start() is None
