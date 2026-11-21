from __future__ import annotations

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.core.models import Space
from apps.resources.models import Area, Authorization, Resource, ResourceCategory

WEEKDAY_CHOICES = [
    (0, _("Monday")),
    (1, _("Tuesday")),
    (2, _("Wednesday")),
    (3, _("Thursday")),
    (4, _("Friday")),
    (5, _("Saturday")),
    (6, _("Sunday")),
]


class SpaceModelForm(forms.ModelForm):
    """Limits every foreign-key choice to the current space and sets ``space``."""

    def __init__(self, *args: Any, space: Space, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.space = space
        self.instance.space = space
        for field in self.fields.values():
            if not isinstance(field, forms.ModelChoiceField):
                continue
            qs = field.queryset
            if qs is None:
                continue
            model = qs.model
            if model is User:
                field.queryset = qs.filter(memberships__space=space).distinct()
            elif any(f.name == "space" for f in model._meta.get_fields()):
                field.queryset = qs.filter(space=space)


class AreaForm(SpaceModelForm):
    weekdays = forms.TypedMultipleChoiceField(
        label=_("Open days"),
        choices=WEEKDAY_CHOICES,
        coerce=int,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    class Meta:
        model = Area
        fields = ["name", "day_start", "day_end", "slot_minutes", "is_restricted", "order"]
        widgets = {
            "day_start": forms.TimeInput(attrs={"type": "time"}),
            "day_end": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.initial.setdefault("weekdays", self.instance.open_weekday_list)

    def save(self, commit: bool = True) -> Area:
        self.instance.open_weekdays = sum(1 << d for d in self.cleaned_data["weekdays"])
        return super().save(commit)


class CategoryForm(SpaceModelForm):
    class Meta:
        model = ResourceCategory
        fields = ["name"]


class ResourceForm(SpaceModelForm):
    class Meta:
        model = Resource
        fields = [
            "name",
            "brand",
            "model",
            "category",
            "area",
            "description",
            "color",
            "order",
            "is_active",
            "access",
            "min_duration_minutes",
            "max_duration_minutes",
            "max_bookings_per_day",
            "max_advance_days",
            "edit_deadline_hours",
            "managers",
        ]
        widgets = {
            "color": forms.TextInput(attrs={"type": "color"}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "managers": forms.CheckboxSelectMultiple,
        }


class AuthorizationForm(SpaceModelForm):
    class Meta:
        model = Authorization
        fields = ["user", "category", "granted_on", "expires_on", "granted_by", "comment"]
        widgets = {
            "granted_on": forms.DateInput(attrs={"type": "date"}),
            "expires_on": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self) -> dict[str, Any]:
        super().clean()
        data = self.cleaned_data
        if (
            data.get("expires_on")
            and data.get("granted_on")
            and data["expires_on"] < data["granted_on"]
        ):
            self.add_error("expires_on", _("Must be after the grant date."))
        user, category = data.get("user"), data.get("category")
        if (
            user
            and category
            and Authorization.objects.filter(user=user, category=category)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            self.add_error("category", _("This user already has this authorization."))
        return data
