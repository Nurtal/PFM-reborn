from __future__ import annotations

from typing import Any

from django import forms
from django.contrib.auth import password_validation
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.core import services
from apps.core.models import Role, SMTPSecurity, Space


class SetupForm(forms.Form):
    token = forms.CharField(
        label=_("Setup token"),
        help_text=_("Printed in the container logs (docker logs pfm)."),
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    site_name = forms.CharField(label=_("Site name"), max_length=120, initial="PFM")

    username = forms.CharField(label=_("Username"), max_length=150)
    email = forms.EmailField(label=_("Email"))
    password1 = forms.CharField(
        label=_("Password"), strip=False, widget=forms.PasswordInput(render_value=False)
    )
    password2 = forms.CharField(
        label=_("Password (again)"), strip=False, widget=forms.PasswordInput
    )

    space_name = forms.CharField(label=_("Platform name"), max_length=120)
    space_slug = forms.SlugField(
        label=_("Short name"), max_length=50, help_text=_("Used in URLs, e.g. imaging.")
    )

    smtp_host = forms.CharField(label=_("SMTP host"), required=False)
    smtp_port = forms.IntegerField(label=_("Port"), initial=587, min_value=1, max_value=65535)
    smtp_security = forms.ChoiceField(
        label=_("Security"),
        choices=SMTPSecurity.choices,
        initial="starttls",
    )
    smtp_username = forms.CharField(label=_("SMTP user"), required=False)
    smtp_password = forms.CharField(
        label=_("SMTP password"), required=False, strip=False, widget=forms.PasswordInput
    )
    smtp_from_email = forms.EmailField(label=_("Sender address"), required=False)

    def __init__(self, *args: Any, require_token: bool = True, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not require_token:
            del self.fields["token"]

    def clean_token(self) -> str:
        token = self.cleaned_data["token"]
        if not services.check_setup_token(token):
            raise forms.ValidationError(_("Invalid setup token."))
        return token

    def clean_space_slug(self) -> str:
        slug = self.cleaned_data["space_slug"]
        if Space.objects.filter(slug=slug).exists():
            raise forms.ValidationError(_("This short name is already used."))
        return slug

    def clean(self) -> dict[str, Any]:
        super().clean()
        data = self.cleaned_data
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", _("The two passwords do not match."))
        elif p1:
            candidate = User(username=data.get("username", ""), email=data.get("email", ""))
            try:
                password_validation.validate_password(p1, candidate)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return data

    def to_setup_data(self) -> services.SetupData:
        d = self.cleaned_data
        return services.SetupData(
            username=d["username"],
            email=d["email"],
            password=d["password1"],
            space_name=d["space_name"],
            space_slug=d["space_slug"],
            site_name=d["site_name"],
            smtp_host=d["smtp_host"],
            smtp_port=d["smtp_port"],
            smtp_username=d["smtp_username"],
            smtp_password=d["smtp_password"],
            smtp_security=d["smtp_security"],
            smtp_from_email=d["smtp_from_email"],
        )


class SMTPTestForm(forms.Form):
    smtp_host = forms.CharField()
    smtp_port = forms.IntegerField(min_value=1, max_value=65535)
    smtp_security = forms.ChoiceField(choices=SMTPSecurity.choices)
    smtp_username = forms.CharField(required=False)
    smtp_password = forms.CharField(required=False, strip=False)
    smtp_from_email = forms.EmailField(required=False)
    email = forms.EmailField()


class AddMemberForm(forms.Form):
    email = forms.EmailField(label=_("User email"))
    role = forms.TypedChoiceField(
        label=_("Role"), choices=Role.choices, coerce=int, initial=Role.USER
    )

    def clean_email(self) -> str:
        email = self.cleaned_data["email"]
        try:
            self.user = User.objects.get(email__iexact=email, is_active=True)
        except User.DoesNotExist:
            raise forms.ValidationError(_("No active account uses this email.")) from None
        return email


class RoleForm(forms.Form):
    role = forms.TypedChoiceField(label=_("Role"), choices=Role.choices, coerce=int)
