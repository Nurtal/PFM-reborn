from __future__ import annotations

from collections.abc import Callable

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _

from apps.accounts import services

SESSION_FLAG = "pfm_mfa_ok"
# Paths an admin without MFA may still reach (to set it up or to leave).
ALLOWED_PREFIXES = ("/accounts/", "/static/", "/healthz", "/i18n/", "/setup/")


class AdminMFARequiredMiddleware:
    """Force privileged users to enrol a second factor before using the app."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = request.user
        if (
            user.is_authenticated
            and not request.session.get(SESSION_FLAG)
            and not request.path.startswith(ALLOWED_PREFIXES)
        ):
            if services.requires_mfa(user) and not services.has_mfa(user):  # type: ignore[arg-type]
                messages.warning(
                    request,
                    _("Two-factor authentication is mandatory for administrators."),
                )
                return redirect("mfa_activate_totp")
            request.session[SESSION_FLAG] = True
        return self.get_response(request)
