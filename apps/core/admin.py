from django import forms
from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from apps.core.models import InstanceSettings, Space, SpaceMembership


class MembershipInline(admin.TabularInline):
    model = SpaceMembership
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Space)
class SpaceAdmin(SimpleHistoryAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "slug")
    inlines = [MembershipInline]


@admin.register(SpaceMembership)
class SpaceMembershipAdmin(SimpleHistoryAdmin):
    list_display = ("user", "space", "role")
    list_filter = ("space", "role")


class InstanceSettingsForm(forms.ModelForm):
    """Secrets are write-only: leave empty to keep the stored value."""

    smtp_password = forms.CharField(required=False, widget=forms.PasswordInput, strip=False)
    ldap_bind_password = forms.CharField(required=False, widget=forms.PasswordInput, strip=False)

    class Meta:
        model = InstanceSettings
        # Admin-only singleton: every other field is meant to be editable.
        exclude = ("smtp_password_encrypted", "ldap_bind_password_encrypted")  # noqa: DJ006

    def save(self, commit: bool = True) -> InstanceSettings:
        instance: InstanceSettings = super().save(commit=False)
        if self.cleaned_data.get("smtp_password"):
            instance.set_smtp_password(self.cleaned_data["smtp_password"])
        if self.cleaned_data.get("ldap_bind_password"):
            instance.set_ldap_bind_password(self.cleaned_data["ldap_bind_password"])
        if commit:
            instance.save()
        return instance


@admin.register(InstanceSettings)
class InstanceSettingsAdmin(SimpleHistoryAdmin):
    form = InstanceSettingsForm

    def has_add_permission(self, request):  # type: ignore[no-untyped-def]
        return False

    def has_delete_permission(self, request, obj=None):  # type: ignore[no-untyped-def]
        return False
