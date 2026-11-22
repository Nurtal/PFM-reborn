import os
import tempfile

# Must run before base settings: keep tests away from the developer's .data.
os.environ.setdefault("PFM_DATA_DIR", tempfile.mkdtemp(prefix="pfm-test-"))

from .base import *  # noqa: F403
from .base import HUEY

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # speed only, tests
HUEY["immediate"] = True
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
AXES_ENABLED = False
ACCOUNT_RATE_LIMITS = False  # type: ignore[assignment]
WHITENOISE_AUTOREFRESH = True
