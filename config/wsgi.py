import logging
import os
import signal
from pathlib import Path

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

application = get_wsgi_application()

# Set by the container entrypoint: migrate etc. here, Django being loaded already.
if os.environ.get("PFM_PREPARE") == "wsgi":
    from apps.core import startup

    try:
        startup.prepare_once(Path(os.environ.get("PFM_RUNTIME_DIR", "/tmp")))  # noqa: S108
    except Exception:
        logging.getLogger("pfm.startup").exception("Start-up failed: stopping the server")
        # Stop the whole server (our parent), not just this worker: a respawned
        # worker would retry forever. Docker's restart policy takes over.
        os.kill(os.getppid(), signal.SIGTERM)
        os._exit(3)

if os.environ.get("PFM_TASKS", "embedded") == "embedded":
    from apps.core import embedded_worker

    embedded_worker.start()
