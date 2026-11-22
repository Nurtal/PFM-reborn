from django.urls import path

from apps.resources import views

app_name = "resources"

urlpatterns = [
    path("", views.ResourceListView.as_view(), name="list"),
    path("new/", views.ResourceCreateView.as_view(), name="create"),
    path("<uuid:pk>/edit/", views.ResourceUpdateView.as_view(), name="update"),
    path("settings/", views.AreaListView.as_view(), name="settings"),
    path("areas/new/", views.AreaCreateView.as_view(), name="area_create"),
    path("areas/<int:pk>/", views.AreaUpdateView.as_view(), name="area_update"),
    path("categories/new/", views.CategoryCreateView.as_view(), name="category_create"),
    path("categories/<int:pk>/", views.CategoryUpdateView.as_view(), name="category_update"),
    path("authorizations/", views.AuthorizationListView.as_view(), name="authorizations"),
    path(
        "authorizations/new/",
        views.AuthorizationCreateView.as_view(),
        name="authorization_create",
    ),
    path(
        "authorizations/<int:pk>/",
        views.AuthorizationUpdateView.as_view(),
        name="authorization_update",
    ),
]
