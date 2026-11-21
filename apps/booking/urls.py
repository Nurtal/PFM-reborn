from django.urls import path

from apps.booking import views

app_name = "booking"

urlpatterns = [
    path("booking/", views.BookingHomeView.as_view(), name="home"),
    path("booking/<uuid:resource_id>/", views.CalendarView.as_view(), name="calendar"),
    path("booking/<uuid:resource_id>/events/", views.EventsView.as_view(), name="events"),
    path("booking/<uuid:resource_id>/new/", views.BookingCreateView.as_view(), name="create"),
    path("booking/<uuid:resource_id>/quote/", views.QuoteView.as_view(), name="quote"),
    path("bookings/<uuid:booking_id>/", views.BookingDetailView.as_view(), name="detail"),
    path("my-bookings/", views.MyBookingsView.as_view(), name="mine"),
    path("closures/", views.ClosureListView.as_view(), name="closures"),
]
