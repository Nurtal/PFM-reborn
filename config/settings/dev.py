from .base import *  # noqa: F403
from .base import HUEY, env

DEBUG = True
HUEY["immediate"] = env.bool("HUEY_IMMEDIATE", default=True)
if not PFM_EMAIL_FROM_ENV:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
WHITENOISE_AUTOREFRESH = True
