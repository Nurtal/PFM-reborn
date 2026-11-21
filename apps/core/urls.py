from django.urls import path

from apps.core import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("setup/", views.setup, name="setup"),
    path("setup/test-smtp/", views.setup_test_smtp, name="setup_test_smtp"),
    path("s/<slug:space_slug>/", views.SpaceDashboardView.as_view(), name="space_dashboard"),
    path("s/<slug:space_slug>/members/", views.MemberListView.as_view(), name="members"),
    path("s/<slug:space_slug>/settings/", views.SpaceSettingsView.as_view(), name="space_settings"),
    path(
        "s/<slug:space_slug>/members/<int:pk>/",
        views.MemberDetailView.as_view(),
        name="member_detail",
    ),
]
