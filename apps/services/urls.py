from django.urls import path

from apps.services import views

app_name = "services"

urlpatterns = [
    path("", views.CatalogueView.as_view(), name="catalogue"),
    path("new/", views.ServiceCreateView.as_view(), name="create"),
    path("<int:pk>/", views.ServiceUpdateView.as_view(), name="update"),
    path("orders/", views.OrderListView.as_view(), name="orders"),
    path("orders/new/", views.OrderCreateView.as_view(), name="order_create"),
    path("orders/<uuid:order_id>/", views.OrderDetailView.as_view(), name="order_detail"),
    path("projects/", views.ProjectListView.as_view(), name="projects"),
    path("projects/new/", views.ProjectCreateView.as_view(), name="project_create"),
    path("projects/<uuid:pk>/", views.ProjectUpdateView.as_view(), name="project_update"),
    path("stock/", views.StockView.as_view(), name="stock"),
]
