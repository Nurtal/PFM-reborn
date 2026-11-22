from __future__ import annotations

from typing import Any, cast

from django import forms
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.generic import TemplateView

from apps.accounts.models import User
from apps.core.permissions import SpaceViewMixin
from apps.mailer import services
from apps.mailer.models import Audience, Mailing
from apps.resources.models import Resource


class MailingForm(forms.ModelForm):
    class Meta:
        model = Mailing
        fields = ["audience", "resource", "subject", "body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 8})}
        labels = {"resource": gettext_lazy("Resource")}

    def __init__(self, *args: Any, space: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.instance.space = space
        self.fields["resource"].queryset = Resource.objects.for_space(space)  # type: ignore[attr-defined]
        self.fields["resource"].required = False

    def clean_subject(self) -> str:
        subject: str = self.cleaned_data["subject"]
        if "\n" in subject or "\r" in subject:  # header injection
            raise forms.ValidationError(_("The subject must fit on one line."))
        return subject

    def clean(self) -> dict[str, Any]:
        super().clean()
        d = self.cleaned_data
        if d.get("audience") == Audience.RESOURCE and not d.get("resource"):
            self.add_error("resource", _("Choose the resource."))
        return d


class MailerView(SpaceViewMixin, TemplateView):
    permission_required = "core.manage_space"
    template_name = "mailer/mailer.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = kwargs.get("form") or MailingForm(space=self.space)
        ctx["history"] = Mailing.objects.for_space(self.space).select_related("sent_by")[:30]
        ctx["preview"] = kwargs.get("preview")
        return ctx

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = MailingForm(request.POST, space=self.space)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        mailing = form.save(commit=False)
        mailing.sent_by = cast(User, request.user)
        if request.POST.get("confirm") != "1":
            # Two steps: show how many people will receive it before sending.
            count = len(services.recipients(mailing))
            return self.render_to_response(self.get_context_data(form=form, preview=count))
        mailing.save()
        services.send(mailing)
        messages.success(
            request, _("Message queued for %(n)d recipients.") % {"n": mailing.recipients_count}
        )
        return redirect("mailer:index", space_slug=self.space.slug)
