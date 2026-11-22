from __future__ import annotations

import logging
import smtplib
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.core.mail import EmailMessage
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import TemplateView

from apps.core import services
from apps.core.email import smtp_backend_from
from apps.core.forms import AddMemberForm, RoleForm, SetupForm, SMTPTestForm
from apps.core.models import InstanceSettings, Role, SpaceMembership
from apps.core.permissions import SpaceViewMixin

logger = logging.getLogger(__name__)


@login_not_required
@never_cache
@require_GET
def healthz(request: HttpRequest) -> JsonResponse:
    try:
        connection.ensure_connection()
    except Exception:
        logger.exception("Health check failed")
        return JsonResponse({"status": "error"}, status=503)
    return JsonResponse({"status": "ok"})


# --- First-run wizard -------------------------------------------------------------


def _require_token() -> bool:
    return not settings.DEBUG


@login_not_required
@never_cache
def setup(request: HttpRequest) -> HttpResponse:
    if services.is_setup_completed():
        return redirect("core:home")
    form = SetupForm(request.POST or None, require_token=_require_token())
    if request.method == "POST" and form.is_valid():
        try:
            admin = services.complete_setup(form.to_setup_data())
        except services.SetupAlreadyCompleted:
            return redirect("core:home")
        login(request, admin, backend="django.contrib.auth.backends.ModelBackend")
        _record_password_auth(request, admin)
        messages.success(
            request, _("Welcome! Last step: secure your administrator account with 2FA.")
        )
        return redirect("mfa_activate_totp")
    if request.method == "GET":
        services.log_setup_token()
    return render(request, "core/setup.html", {"form": form})


def _record_password_auth(request: HttpRequest, user: Any) -> None:
    """Tell allauth the admin just typed their password, so enrolling 2FA right
    after the wizard does not ask for it again. Internal allauth API: if it
    moves, the only cost is one extra password prompt."""
    try:
        from allauth.account.internal.flows.login import record_authentication
    except ImportError:  # pragma: no cover
        return
    record_authentication(request, user, method="password", reauthenticated=True)


@login_not_required
@never_cache
@require_POST
def setup_test_smtp(request: HttpRequest) -> HttpResponse:
    """Send a test mail with the SMTP values typed in the wizard (HTMX)."""
    if services.is_setup_completed() and not request.user.is_superuser:
        return HttpResponse(status=403)
    if (
        _require_token()
        and not services.is_setup_completed()
        and not services.check_setup_token(request.POST.get("token", ""))
    ):
        error = _("Invalid setup token.")
        return render(request, "core/_smtp_result.html", {"ok": False, "error": error})
    form = SMTPTestForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "core/_smtp_result.html",
            {"ok": False, "error": _("Fill in the SMTP host, port and a recipient email.")},
        )
    d = form.cleaned_data
    instance = InstanceSettings(
        smtp_host=d["smtp_host"],
        smtp_port=d["smtp_port"],
        smtp_security=d["smtp_security"],
        smtp_username=d["smtp_username"],
    )
    instance.set_smtp_password(d["smtp_password"])
    message = EmailMessage(
        subject=_("PFM test email"),
        body=_("Your SMTP settings work."),
        from_email=d["smtp_from_email"] or settings.DEFAULT_FROM_EMAIL,
        to=[d["email"]],
        connection=smtp_backend_from(instance, fail_silently=False),
    )
    try:
        message.send()
    except (TimeoutError, smtplib.SMTPException, OSError) as exc:
        return render(request, "core/_smtp_result.html", {"ok": False, "error": str(exc)})
    return render(request, "core/_smtp_result.html", {"ok": True})


# --- Spaces ----------------------------------------------------------------------


def home(request: HttpRequest) -> HttpResponse:
    spaces = services.spaces_for(request.user)  # type: ignore[arg-type]
    if len(spaces) == 1 and not request.user.is_superuser:
        return redirect("core:space_dashboard", space_slug=spaces[0].slug)
    return render(request, "core/home.html", {"spaces": spaces})


class SpaceDashboardView(SpaceViewMixin, TemplateView):
    permission_required = "core.view_space"
    template_name = "core/space_dashboard.html"


class MemberListView(SpaceViewMixin, TemplateView):
    permission_required = "core.administer_space"
    template_name = "core/members.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx["memberships"] = (
            SpaceMembership.objects.filter(space=self.space)
            .select_related("user")
            .order_by("-role", "user__last_name", "user__username")
        )
        ctx["form"] = kwargs.get("form") or AddMemberForm()
        ctx["roles"] = Role.choices
        return ctx

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = AddMemberForm(request.POST)
        if form.is_valid():
            try:
                services.add_member(self.space, form.user, Role(form.cleaned_data["role"]))
            except services.LastAdminError:
                messages.error(request, _("A space needs at least one administrator."))
            else:
                messages.success(request, _("Member saved."))
            return redirect("core:members", space_slug=self.space.slug)
        return self.render_to_response(self.get_context_data(form=form))


class MemberDetailView(SpaceViewMixin, View):
    """Change the role of (POST) or remove (POST action=remove) one member."""

    permission_required = "core.administer_space"
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        membership = get_object_or_404(SpaceMembership, space=self.space, pk=kwargs["pk"])
        try:
            if request.POST.get("action") == "remove":
                services.remove_member(membership)
                messages.success(request, _("Member removed."))
            else:
                form = RoleForm(request.POST)
                if form.is_valid():
                    services.change_role(membership, Role(form.cleaned_data["role"]))
                    messages.success(request, _("Role updated."))
        except services.LastAdminError:
            messages.error(request, _("A space needs at least one administrator."))
        return redirect("core:members", space_slug=self.space.slug)
