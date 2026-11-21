from django.urls import path

from apps.mailer import views

app_name = "mailer"

urlpatterns = [path("", views.MailerView.as_view(), name="index")]
