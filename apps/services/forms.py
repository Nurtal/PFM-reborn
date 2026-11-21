from __future__ import annotations

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.clients.models import Client
from apps.resources.forms import SpaceModelForm
from apps.services.models import Order, ServiceItem


class ServiceItemForm(SpaceModelForm):
    class Meta:
        model = ServiceItem
        fields = ["name", "unit", "description", "order", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class OrderForm(SpaceModelForm):
    class Meta:
        model = Order
        fields = ["client", "requester", "title", "note"]
        widgets = {"note": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Client.objects.filter(  # type: ignore[attr-defined]
            space=self.space, is_active=True
        )


class OrderLineForm(forms.Form):
    item = forms.ModelChoiceField(label=_("Service"), queryset=ServiceItem.objects.none())
    quantity = forms.DecimalField(label=_("Quantity"), max_digits=10, decimal_places=2, min_value=0)
    performed_on = forms.DateField(
        label=_("Performed on"), widget=forms.DateInput(attrs={"type": "date"}), required=False
    )
    comment = forms.CharField(label=_("Comment"), max_length=200, required=False)

    def __init__(self, *args: Any, space: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = ServiceItem.objects.filter(space=space, is_active=True)  # type: ignore[attr-defined]
