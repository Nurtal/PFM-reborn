from django.urls import path

from apps.clients import views

app_name = "clients"

urlpatterns = [
    path("", views.ClientListView.as_view(), name="list"),
    path("new/", views.ClientCreateView.as_view(), name="create"),
    path("<uuid:pk>/", views.ClientUpdateView.as_view(), name="update"),
    path("prices/", views.PriceMatrixView.as_view(), name="prices"),
    path("grids/new/", views.GridCreateView.as_view(), name="grid_create"),
    path("grids/<int:pk>/", views.GridUpdateView.as_view(), name="grid_update"),
    path("packages/new/", views.PackageCreateView.as_view(), name="package_create"),
    path("packages/<int:pk>/", views.PackageUpdateView.as_view(), name="package_update"),
]
