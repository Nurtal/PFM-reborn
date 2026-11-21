from __future__ import annotations

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.clients.models import Client, Package, PricingGrid
from apps.resources.forms import WEEKDAY_CHOICES, SpaceModelForm


class PricingGridForm(SpaceModelForm):
    weekend = forms.TypedMultipleChoiceField(
        label=_("Weekend days"),
        choices=WEEKDAY_CHOICES,
        coerce=int,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    class Meta:
        model = PricingGrid
        fields = [
            "name",
            "order",
            "night_rate_enabled",
            "night_start",
            "night_end",
            "weekend_rate_enabled",
        ]
        widgets = {
            "night_start": forms.TimeInput(attrs={"type": "time"}),
            "night_end": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.initial.setdefault(
            "weekend", [d for d in range(7) if self.instance.weekend_days & (1 << d)]
        )

    def save(self, commit: bool = True) -> PricingGrid:
        self.instance.weekend_days = sum(1 << d for d in self.cleaned_data["weekend"])
        return super().save(commit)


class ClientForm(SpaceModelForm):
    class Meta:
        model = Client
        fields = [
            "name",
            "organization",
            "pricing_grid",
            "contact_name",
            "email",
            "phone",
            "reference",
            "vat_rate",
            "invoice_address",
            "is_active",
            "users",
        ]
        widgets = {
            "invoice_address": forms.Textarea(attrs={"rows": 3}),
            "users": forms.CheckboxSelectMultiple,
        }


class PackageForm(SpaceModelForm):
    class Meta:
        model = Package
        fields = ["resource", "name", "duration_minutes", "is_active"]
