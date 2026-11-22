from __future__ import annotations

import time
from collections.abc import Callable

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _

from apps.accounts import services

SESSION_KEY = "pfm_mfa_checked_until"
# Re-check periodically so a user promoted to admin, or an admin who removed
# their authenticator, is caught within minutes instead of at the next login.
RECHECK_SECONDS = 300
# Paths an admin without MFA may still reach (to set it up or to leave).
ALLOWED_PREFIXES = ("/accounts/", "/static/", "/healthz", "/i18n/", "/setup/", "/api/")


class AdminMFARequiredMiddleware:
    """Force privileged users to enrol a second factor (PFM_REQUIRE_ADMIN_MFA)."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = request.user
        if (
            settings.PFM_REQUIRE_ADMIN_MFA
            and user.is_authenticated
            and request.session.get(SESSION_KEY, 0) < time.time()
            and not request.path.startswith(ALLOWED_PREFIXES)
        ):
            if services.requires_mfa(user) and not services.has_mfa(user):  # type: ignore[arg-type]
                messages.warning(
                    request,
                    _("Two-factor authentication is mandatory for administrators."),
                )
                return redirect("mfa_activate_totp")
            request.session[SESSION_KEY] = time.time() + RECHECK_SECONDS
        return self.get_response(request)
