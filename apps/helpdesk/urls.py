from django.urls import path

from apps.helpdesk import views

app_name = "helpdesk"

urlpatterns = [
    path("", views.TicketListView.as_view(), name="list"),
    path("mailbox/", views.MailboxView.as_view(), name="mailbox"),
    path("<int:number>/", views.TicketDetailView.as_view(), name="detail"),
]
