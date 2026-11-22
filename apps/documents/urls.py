from django.urls import path

from apps.documents import views

app_name = "documents"

urlpatterns = [
    path("", views.DocumentListView.as_view(), name="list"),
    path("<uuid:doc_id>/", views.DocumentDownloadView.as_view(), name="download"),
]
