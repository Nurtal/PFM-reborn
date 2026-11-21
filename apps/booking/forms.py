from __future__ import annotations

from typing import Any, cast

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.booking import services
from apps.booking.models import Booking
from apps.core.models import Role
from apps.resources.models import Resource

DATETIME_WIDGET = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class BookingForm(forms.Form):
    """Plain form (not a ModelForm): the instance is never mutated before the
    service has checked that the booking may still be changed."""

    start = forms.DateTimeField(label=_("Start"), widget=DATETIME_WIDGET)
    end = forms.DateTimeField(label=_("End"), widget=DATETIME_WIDGET)
    title = forms.CharField(label=_("Title"), max_length=200, required=False)
    note = forms.CharField(
        label=_("Note"), required=False, widget=forms.Textarea(attrs={"rows": 2})
    )
    for_user = forms.ModelChoiceField(
        label=_("Book for"), queryset=User.objects.none(), required=False
    )

    def __init__(
        self,
        *args: Any,
        actor: Any,
        resource: Resource,
        instance: Booking | None = None,
        **kwargs: Any,
    ) -> None:
        if instance is not None:
            kwargs.setdefault(
                "initial",
                {
                    "start": timezone.localtime(instance.start),
                    "end": timezone.localtime(instance.end),
                    "title": instance.title,
                    "note": instance.note,
                },
            )
        super().__init__(*args, **kwargs)
        for_user = cast(forms.ModelChoiceField, self.fields["for_user"])
        if instance is None and services.is_resource_manager(actor, resource):
            for_user.queryset = (
                User.objects.filter(
                    is_active=True,
                    memberships__space=resource.space,
                    memberships__role__gte=Role.USER,
                )
                .distinct()
                .order_by("last_name", "username")
            )
            for_user.empty_label = _("Myself")
        else:
            del self.fields["for_user"]

    def clean(self) -> dict[str, Any]:
        super().clean()
        data = self.cleaned_data
        if data.get("start") and data.get("end") and data["end"] <= data["start"]:
            self.add_error("end", _("The end must be after the start."))
        return data
