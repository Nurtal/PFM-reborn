from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path
from django.views.i18n import set_language

from apps.api.api import api
from apps.api.views import tokens
from apps.booking.views import ical_feed
from apps.catalog.views import catalogue

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("me/data/", include("apps.accounts.urls")),
    path("me/tokens/", tokens, name="api_tokens"),
    path("api/v1/", api.urls),
    path("i18n/setlang/", login_not_required(set_language), name="set_language"),
    path("ical/<str:token>.ics", ical_feed, name="ical_feed"),
    path("c/<slug:space_slug>/", catalogue, name="catalogue"),
    path("s/<slug:space_slug>/resources/", include("apps.resources.urls")),
    path("s/<slug:space_slug>/clients/", include("apps.clients.urls")),
    path("s/<slug:space_slug>/invoices/", include("apps.invoices.urls")),
    path("s/<slug:space_slug>/services/", include("apps.services.urls")),
    path("s/<slug:space_slug>/statistics/", include("apps.statistics.urls")),
    path("s/<slug:space_slug>/documents/", include("apps.documents.urls")),
    path("s/<slug:space_slug>/mailer/", include("apps.mailer.urls")),
    path("s/<slug:space_slug>/helpdesk/", include("apps.helpdesk.urls")),
    path("s/<slug:space_slug>/", include("apps.booking.urls")),
    path("", include("apps.core.urls")),
]
