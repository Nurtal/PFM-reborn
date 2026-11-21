from __future__ import annotations

import json
from typing import cast

from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts import services
from apps.accounts.models import User


def my_data(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/my_data.html")


def export_data(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    payload = json.dumps(services.export_user_data(user), ensure_ascii=False, indent=2)
    response = HttpResponse(payload, content_type="application/json; charset=utf-8")
    stamp = timezone.localdate().isoformat()
    response["Content-Disposition"] = f'attachment; filename="pfm-my-data-{stamp}.json"'
    return response


@require_POST
def delete_account(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    if not user.check_password(request.POST.get("password", "")):
        messages.error(request, _("Wrong password: your account was not deleted."))
        return redirect("accounts:my_data")
    if user.is_superuser:
        messages.error(request, _("Super-administrators must be removed by another administrator."))
        return redirect("accounts:my_data")
    services.anonymize(user)
    logout(request)
    messages.success(request, _("Your account and personal data have been deleted."))
    return redirect("account_login")
