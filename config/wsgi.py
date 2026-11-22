import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

application = get_wsgi_application()

if os.environ.get("PFM_TASKS", "embedded") == "embedded":
    from apps.core import embedded_worker

    embedded_worker.start()
