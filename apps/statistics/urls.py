from django.urls import path

from apps.statistics import views

app_name = "statistics"

urlpatterns = [
    path("", views.StatisticsView.as_view(), name="dashboard"),
    path("<str:table>.csv", views.StatisticsExportView.as_view(), name="export"),
]
