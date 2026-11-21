"""Settings shared by every environment.

Configuration comes from environment variables with sane defaults; only
``PFM_URL`` is recommended in production. See README §10.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import environ
from csp.constants import NONCE, NONE, SELF
from django.utils.translation import gettext_lazy as _

from config.bootstrap import data_dir, load_or_create_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()

# --- Data directory: the single volume ----------------------------------------
DATA_DIR = data_dir(default=BASE_DIR / ".data")
MEDIA_ROOT = DATA_DIR / "media"
BACKUP_DIR = DATA_DIR / "backups"
for _dir in (MEDIA_ROOT, BACKUP_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

SECRET_KEY = load_or_create_secret_key(DATA_DIR)
DEBUG = False

# --- Public URL drives hosts, CSRF and cookie security --------------------------
PFM_URL = env.str("PFM_URL", default="http://localhost:8000").rstrip("/")
_url = urlsplit(PFM_URL)
PFM_HTTPS = _url.scheme == "https"
# localhost/127.0.0.1 are always allowed so the in-container healthcheck works.
ALLOWED_HOSTS = sorted({_url.hostname or "localhost", "localhost", "127.0.0.1"})
CSRF_TRUSTED_ORIGINS = [f"{_url.scheme}://{_url.netloc}"]

if env.bool("PFM_BEHIND_PROXY", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

# --- Applications ----------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    # Third party
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "axes",
    "csp",
    "django_cotton",
    "django_htmx",
    "huey.contrib.djhuey",
    "rules.apps.AutodiscoverRulesConfig",
    "simple_history",
    # Project
    "apps.accounts",
    "apps.core",
    "apps.resources",
    "apps.booking",
    "apps.clients",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "csp.middleware.CSPMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "apps.core.middleware.SetupRequiredMiddleware",
    # Deny by default: every view needs a login unless marked @login_not_required.
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "apps.accounts.middleware.AdminMFARequiredMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    # Axes must be last.
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "csp.context_processors.nonce",
                "apps.core.context_processors.pfm",
            ],
        },
    },
]

# --- Database ----------------------------------------------------------------------
# An empty DATABASE_URL (e.g. from compose) means the SQLite default.
DATABASES = {
    "default": env.db_url_config(
        env.str("DATABASE_URL", default="") or f"sqlite:///{DATA_DIR / 'db.sqlite3'}"
    ),
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    DATABASES["default"]["OPTIONS"] = {
        # IMMEDIATE takes the write lock at BEGIN: concurrent writers are
        # serialised, which makes check-then-insert (booking overlap) safe.
        "transaction_mode": "IMMEDIATE",
        "timeout": 5,
        "init_command": (
            "PRAGMA journal_mode=WAL;"
            "PRAGMA synchronous=NORMAL;"
            "PRAGMA foreign_keys=ON;"
            "PRAGMA temp_store=MEMORY;"
            "PRAGMA mmap_size=134217728;"
            "PRAGMA journal_size_limit=27103364;"
            "PRAGMA cache_size=2000;"
        ),
    }
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# No external cache service.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# --- Authentication ---------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "rules.permissions.ObjectPermissionBackend",
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "core:home"

# allauth
ACCOUNT_LOGIN_METHODS = {"username", "email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "username*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "optional"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = False
ACCOUNT_SESSION_REMEMBER = None
ACCOUNT_LOGIN_ON_PASSWORD_RESET = False
ACCOUNT_RATE_LIMITS = {"login_failed": "10/m/ip,5/5m/key"}
ACCOUNT_ADAPTER = "apps.accounts.adapters.AccountAdapter"
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes", "webauthn"]
MFA_PASSKEY_LOGIN_ENABLED = True
MFA_TOTP_ISSUER = "PFM"
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = False

# django-axes: lock a username+IP pair after repeated failures.
AXES_FAILURE_LIMIT = 10
AXES_COOLOFF_TIME = 1  # hours
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True

# --- Sessions, cookies, security headers -----------------------------------------
SESSION_COOKIE_AGE = 60 * 60 * 12
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = PFM_HTTPS
CSRF_COOKIE_SECURE = PFM_HTTPS
CSRF_COOKIE_SAMESITE = "Lax"
LANGUAGE_COOKIE_SECURE = PFM_HTTPS
LANGUAGE_COOKIE_SAMESITE = "Lax"

SECURE_SSL_REDIRECT = PFM_HTTPS
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SECURE_HSTS_SECONDS = 31536000 if PFM_HTTPS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = PFM_HTTPS
# HSTS preload commits the whole (institutional) domain: an opt-in decision for
# the domain owner, not an application default.
SILENCED_SYSTEM_CHECKS = ["security.W021"]
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": [SELF],
        "script-src": [SELF, NONCE],
        "style-src": [SELF, NONCE],
        "img-src": [SELF, "data:"],
        "font-src": [SELF, "data:"],  # FullCalendar ships its icon font inline
        "connect-src": [SELF],
        "object-src": [NONE],
        "base-uri": [SELF],
        "form-action": [SELF],
        "frame-ancestors": [NONE],
    },
}

PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"

# --- i18n / time --------------------------------------------------------------------
LANGUAGE_CODE = "fr"
LANGUAGES = [("fr", _("French")), ("en", _("English"))]
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_I18N = True
TIME_ZONE = env.str("PFM_TIME_ZONE", default="Europe/Paris")
USE_TZ = True

# --- Static & media --------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    # Uploads are never served directly: downloads go through permission-checked views.
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# --- Email --------------------------------------------------------------------------
# EMAIL_URL wins when set; otherwise SMTP settings saved in the admin UI are used.
_email_url = env.str("EMAIL_URL", default="")
if _email_url:
    vars().update(env.email_url("EMAIL_URL"))
    PFM_EMAIL_FROM_ENV = True
else:
    EMAIL_BACKEND = "apps.core.email.InstanceSMTPBackend"
    PFM_EMAIL_FROM_ENV = False
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default=f"PFM <noreply@{_url.hostname}>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# --- Background tasks (Huey, SQLite storage) -----------------------------------
HUEY = {
    "huey_class": "huey.SqliteHuey",
    "name": "pfm",
    "filename": str(DATA_DIR / "tasks.db"),
    "immediate": env.bool("HUEY_IMMEDIATE", default=False),
    "consumer": {"workers": 2, "worker_type": "thread"},
}

# --- Backups ---------------------------------------------------------------------
PFM_BACKUP_RETENTION_DAYS = env.int("PFM_BACKUP_RETENTION_DAYS", default=14)

# --- Logging (JSON-friendly single line to stdout) --------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "line": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "line"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
        "huey": {"level": "INFO"},
    },
}
