"""Run the Huey consumer inside the web process (default in the image).

One Python process instead of two saves ~70 MB of RAM: Django is loaded once.
Workers and the scheduler are threads, exactly as with ``run_huey``; only
signal handling is left to the web server. Set ``PFM_TASKS=separate`` to go
back to a dedicated ``run_huey`` process (heavy task loads).
"""

from __future__ import annotations

import atexit
import logging
import threading
import time

from huey.consumer import Consumer, ConsumerStopped

logger = logging.getLogger(__name__)
_started = threading.Lock()
_consumer: Consumer | None = None


class EmbeddedConsumer(Consumer):
    def _set_signal_handlers(self) -> None:
        """The web server owns the process signals."""


def start(workers: int = 2) -> Consumer | None:
    """Start the consumer threads once per process; returns None if already running."""
    global _consumer
    if not _started.acquire(blocking=False):
        return None
    from huey.contrib.djhuey import HUEY

    if HUEY.immediate:  # tests / dev: tasks run inline, nothing to consume
        return None
    # run_huey discovers every app's tasks.py; do the same, or queued tasks
    # would fail with "not found in TaskRegistry".
    from django.utils.module_loading import autodiscover_modules

    autodiscover_modules("tasks")
    consumer = EmbeddedConsumer(HUEY, workers=workers, worker_type="thread", periodic=True)
    consumer.start()

    def supervise() -> None:
        health = time.monotonic()
        while True:
            try:
                health = consumer.loop(health)
            except ConsumerStopped:
                return

    threading.Thread(target=supervise, name="huey-supervisor", daemon=True).start()

    def shutdown() -> None:
        if not consumer.stop_flag.is_set():  # not already stopped
            consumer.stop(graceful=True)

    atexit.register(shutdown)
    _consumer = consumer
    logger.info("Huey consumer running inside the web process (%d threads)", workers)
    return consumer
