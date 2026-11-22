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


@admin.register(InstanceSettings)
class InstanceSettingsAdmin(SimpleHistoryAdmin):
    exclude = ("smtp_password_encrypted",)

    def has_add_permission(self, request):  # type: ignore[no-untyped-def]
        return False

    def has_delete_permission(self, request, obj=None):  # type: ignore[no-untyped-def]
        return False
