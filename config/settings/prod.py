from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import env

DEBUG = False
if env.bool("DJANGO_DEBUG", default=False):
    raise ImproperlyConfigured("DEBUG must never be enabled with the production settings.")
