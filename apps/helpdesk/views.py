from __future__ import annotations

from typing import Any, cast

from django import forms
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.generic import TemplateView

from apps.accounts.models import User
from apps.core.models import Role
from apps.core.permissions import SpaceViewMixin
from apps.helpdesk import services
from apps.helpdesk.models import Mailbox, Ticket, TicketStatus


class MailboxForm(forms.ModelForm):
    password = forms.CharField(
        label=gettext_lazy("IMAP password"),
        required=False,
        strip=False,
        widget=forms.PasswordInput,
        help_text=gettext_lazy("Leave empty to keep the current one."),
    )

    class Meta:
        model = Mailbox
        fields = [
            "address",
            "imap_host",
            "imap_port",
            "imap_ssl",
            "username",
            "folder",
            "is_active",
        ]


class TicketListView(SpaceViewMixin, TemplateView):
    permission_required = "core.manage_space"
    template_name = "helpdesk/list.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        status = self.request.GET.get("status", TicketStatus.OPEN)
        tickets = Ticket.objects.for_space(self.space).select_related("assignee")
        if status in TicketStatus.values:
            tickets = tickets.filter(status=status)
        ctx.update(
            tickets=tickets[:200],
            status=status,
            statuses=TicketStatus.choices,
            mailbox=Mailbox.objects.filter(space=self.space).first(),
        )
        return ctx


class TicketDetailView(SpaceViewMixin, TemplateView):
    permission_required = "core.manage_space"
    template_name = "helpdesk/detail.html"

    def get_ticket(self) -> Ticket:
        return get_object_or_404(Ticket.objects.for_space(self.space), number=self.kwargs["number"])

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ticket = self.get_ticket()
        ctx.update(
            ticket=ticket,
            thread=ticket.messages.select_related("author"),
            statuses=TicketStatus.choices,
            staff=User.objects.filter(
                memberships__space=self.space, memberships__role__gte=Role.MANAGER, is_active=True
            ).distinct(),
        )
        return ctx

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        ticket = self.get_ticket()
        if request.POST.get("action") == "reply":
            body = request.POST.get("body", "").strip()
            if body:
                services.reply(
                    ticket, cast(User, request.user), body, close="close" in request.POST
                )
                messages.success(request, _("Reply sent."))
        else:
            status = request.POST.get("status")
            if status in TicketStatus.values:
                ticket.status = status
            assignee = request.POST.get("assignee")
            ticket.assignee = (
                User.objects.filter(
                    pk=assignee, memberships__space=self.space, memberships__role__gte=Role.MANAGER
                ).first()
                if assignee
                else None
            )
            ticket.save(update_fields=["status", "assignee", "updated_at"])
            messages.success(request, _("Ticket updated."))
        return redirect("helpdesk:detail", space_slug=self.space.slug, number=ticket.number)


class MailboxView(SpaceViewMixin, TemplateView):
    permission_required = "core.administer_space"
    template_name = "resources/form.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            form=kwargs.get("form")
            or MailboxForm(instance=Mailbox.objects.filter(space=self.space).first()),
            title=_("Helpdesk mailbox"),
            cancel_url=f"/s/{self.space.slug}/helpdesk/",
        )
        return ctx

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        instance = Mailbox.objects.filter(space=self.space).first() or Mailbox(space=self.space)
        form = MailboxForm(request.POST, instance=instance)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        mailbox = form.save(commit=False)
        if form.cleaned_data["password"]:
            mailbox.set_password(form.cleaned_data["password"])
        mailbox.save()
        messages.success(request, _("Saved."))
        return redirect("helpdesk:list", space_slug=self.space.slug)
