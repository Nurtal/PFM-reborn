from __future__ import annotations

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.clients.models import Client
from apps.resources.forms import SpaceModelForm
from apps.services.models import Cabinet, Order, Project, ServiceItem, Shelf


class ServiceItemForm(SpaceModelForm):
    class Meta:
        model = ServiceItem
        fields = ["name", "unit", "description", "order", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class OrderForm(SpaceModelForm):
    class Meta:
        model = Order
        fields = ["client", "project", "requester", "title", "note"]
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


class ProjectForm(SpaceModelForm):
    class Meta:
        model = Project
        fields = ["name", "client", "lead", "start_date", "end_date", "status", "description"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self) -> dict[str, Any]:
        super().clean()
        d = self.cleaned_data
        if d.get("start_date") and d.get("end_date") and d["end_date"] < d["start_date"]:
            self.add_error("end_date", _("The end must be after the start."))
        return d


class CabinetForm(SpaceModelForm):
    class Meta:
        model = Cabinet
        fields = ["name", "location"]


class ShelfForm(forms.Form):
    cabinet = forms.ModelChoiceField(label=_("Cabinet"), queryset=Cabinet.objects.none())
    name = forms.CharField(label=_("Name"), max_length=120)
    capacity = forms.IntegerField(label=_("Capacity"), min_value=1, required=False)

    def __init__(self, *args: Any, space: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["cabinet"].queryset = Cabinet.objects.filter(space=space)  # type: ignore[attr-defined]


class StockEntryForm(forms.Form):
    shelf = forms.ModelChoiceField(label=_("Shelf"), queryset=Shelf.objects.none())
    client = forms.ModelChoiceField(label=_("Client"), queryset=Client.objects.none())
    label = forms.CharField(label=_("Label"), max_length=200)
    quantity = forms.IntegerField(label=_("Quantity"), min_value=1, initial=1)
    note = forms.CharField(label=_("Note"), max_length=200, required=False)

    def __init__(self, *args: Any, space: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        shelves = Shelf.objects.filter(cabinet__space=space).select_related("cabinet")
        self.fields["shelf"].queryset = shelves  # type: ignore[attr-defined]
        self.fields["client"].queryset = Client.objects.filter(space=space, is_active=True)  # type: ignore[attr-defined]
