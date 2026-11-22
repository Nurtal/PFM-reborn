from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("", views.my_data, name="my_data"),
    path("export.json", views.export_data, name="export"),
    path("delete/", views.delete_account, name="delete"),
]
