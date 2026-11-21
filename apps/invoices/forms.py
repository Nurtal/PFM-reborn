from __future__ import annotations

import datetime as dt
from typing import Any

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.clients.models import Client
from apps.core.models import Space
from apps.invoices.models import InvoiceSettings

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def previous_month() -> tuple[dt.date, dt.date]:
    first_this_month = timezone.localdate().replace(day=1)
    end = first_this_month - dt.timedelta(days=1)
    return end.replace(day=1), end


class InvoiceRunForm(forms.Form):
    period_start = forms.DateField(label=_("From"), widget=DATE)
    period_end = forms.DateField(label=_("To"), widget=DATE)
    client = forms.ModelChoiceField(
        label=_("Client"),
        queryset=Client.objects.none(),
        required=False,
        empty_label=_("Every client with bookings to bill"),
    )

    def __init__(self, *args: Any, space: Space, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        start, end = previous_month()
        self.fields["period_start"].initial = start
        self.fields["period_end"].initial = end
        self.fields["client"].queryset = Client.objects.filter(space=space, is_active=True)  # type: ignore[attr-defined]

    def clean(self) -> dict[str, Any]:
        super().clean()
        d = self.cleaned_data
        if d.get("period_start") and d.get("period_end") and d["period_end"] < d["period_start"]:
            self.add_error("period_end", _("The period end must be after its start."))
        return d


class LineForm(forms.Form):
    description = forms.CharField(label=_("Description"), max_length=300)
    quantity = forms.DecimalField(label=_("Quantity"), max_digits=10, decimal_places=2)
    unit = forms.CharField(label=_("Unit"), max_length=20, required=False)
    unit_price = forms.DecimalField(label=_("Unit price"), max_digits=12, decimal_places=3)


class InvoiceSettingsForm(forms.ModelForm):
    class Meta:
        model = InvoiceSettings
        fields = [
            "issuer_name",
            "issuer_address",
            "legal_mentions",
            "payment_details",
            "footer",
            "vat_rate",
            "payment_terms_days",
            "number_prefix",
            "credit_prefix",
        ]
        widgets = {
            f: forms.Textarea(attrs={"rows": 3})
            for f in ("issuer_address", "legal_mentions", "payment_details", "footer")
        }
