from __future__ import annotations

from typing import Any, cast

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.booking import services
from apps.booking.models import Booking, Closure
from apps.clients.models import Client, Package
from apps.clients.services import clients_for
from apps.core.models import Role
from apps.resources.models import Area, Resource

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
    client = forms.ModelChoiceField(
        label=_("Client account"), queryset=Client.objects.none(), required=False
    )
    package = forms.ModelChoiceField(
        label=_("Package"), queryset=Package.objects.none(), required=False
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
                    "client": instance.client_id,
                    "package": instance.package_id,
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

        # Billing: the service re-checks; here we only offer sensible choices.
        owner = instance.user if instance is not None else actor
        clients = clients_for(actor, resource.space)
        if not services.is_resource_manager(actor, resource):
            clients = clients.filter(pk__in=clients_for(owner, resource.space).values("pk"))
        client_field = cast(forms.ModelChoiceField, self.fields["client"])
        client_field.queryset = clients
        if clients.exists():
            if clients.count() == 1 and not self.initial.get("client"):
                self.initial["client"] = clients.first().pk  # type: ignore[union-attr]
        else:
            del self.fields["client"]
        packages = Package.objects.filter(resource=resource, is_active=True)
        if packages.exists():
            package_field = cast(forms.ModelChoiceField, self.fields["package"])
            package_field.queryset = packages
            package_field.empty_label = _("Hourly rate")
        else:
            del self.fields["package"]

    def clean(self) -> dict[str, Any]:
        super().clean()
        data = self.cleaned_data
        if data.get("start") and data.get("end") and data["end"] <= data["start"]:
            self.add_error("end", _("The end must be after the start."))
        return data


class ClosureForm(forms.ModelForm):
    class Meta:
        model = Closure
        fields = ["resource", "area", "start", "end", "reason"]
        widgets = {"start": DATETIME_WIDGET, "end": DATETIME_WIDGET}
        help_texts = {"area": _("Leave the resource empty to block every resource of the area.")}

    def __init__(self, *args: Any, space: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.instance.space = space
        cast(forms.ModelChoiceField, self.fields["resource"]).queryset = Resource.objects.for_space(
            space
        )
        cast(forms.ModelChoiceField, self.fields["area"]).queryset = Area.objects.for_space(space)

    def clean(self) -> dict[str, Any]:
        super().clean()
        data = self.cleaned_data
        if not data.get("resource") and not data.get("area"):
            raise forms.ValidationError(_("Choose a resource or an area."))
        if (
            data.get("resource")
            and data.get("area")
            and data["resource"].area_id != data["area"].pk
        ):
            self.add_error("area", _("The resource is not in this area."))
        if data.get("start") and data.get("end") and data["end"] <= data["start"]:
            self.add_error("end", _("The end must be after the start."))
        return data
