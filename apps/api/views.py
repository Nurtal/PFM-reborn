from __future__ import annotations

import datetime as dt
from typing import cast

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.api.models import ApiToken


def tokens(request: HttpRequest) -> HttpResponse:
    """Create (shown once) and revoke personal API tokens."""
    user = cast(User, request.user)
    new_key = None
    if request.method == "POST":
        if "revoke" in request.POST:
            ApiToken.objects.filter(user=user, pk=request.POST.get("revoke", "0")).delete()
            messages.success(request, _("Token revoked."))
            return redirect("api_tokens")
        name = request.POST.get("name", "").strip()[:100] or _("API token")
        days = request.POST.get("days", "90")
        expires = (
            timezone.now() + dt.timedelta(days=int(days)) if days.isdigit() and int(days) else None
        )
        _token, new_key = ApiToken.issue(user, name, expires)
    return render(
        request,
        "api/tokens.html",
        {"tokens": ApiToken.objects.filter(user=user), "new_key": new_key},
    )
