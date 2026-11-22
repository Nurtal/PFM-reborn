from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path
from django.views.i18n import set_language

from apps.booking.views import ical_feed

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("i18n/setlang/", login_not_required(set_language), name="set_language"),
    path("ical/<str:token>.ics", ical_feed, name="ical_feed"),
    path("s/<slug:space_slug>/resources/", include("apps.resources.urls")),
    path("s/<slug:space_slug>/clients/", include("apps.clients.urls")),
    path("s/<slug:space_slug>/invoices/", include("apps.invoices.urls")),
    path("s/<slug:space_slug>/services/", include("apps.services.urls")),
    path("s/<slug:space_slug>/", include("apps.booking.urls")),
    path("", include("apps.core.urls")),
]
