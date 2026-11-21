from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.contrib.auth.middleware import LoginRequiredMiddleware as BaseLoginRequired
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from apps.core import services

SETUP_EXEMPT_PREFIXES = ("/setup/", "/static/", "/healthz", "/i18n/", "/api/")


class SetupRequiredMiddleware:
    """Send every request to the first-run wizard until it has been completed."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not request.path.startswith(SETUP_EXEMPT_PREFIXES) and not services.is_setup_completed():
            return redirect("core:setup")
        response = self.get_response(request)
        response.headers.setdefault("Permissions-Policy", settings.PERMISSIONS_POLICY)
        return response


class LoginRequiredMiddleware(BaseLoginRequired):
    """Deny by default, except the token-authenticated API (it has its own auth)."""

    def process_view(
        self, request: HttpRequest, view_func: Any, view_args: Any, view_kwargs: Any
    ) -> Any:
        if request.path.startswith("/api/"):
            return None
        return super().process_view(request, view_func, view_args, view_kwargs)
