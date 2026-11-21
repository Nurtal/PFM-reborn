from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path
from django.views.i18n import set_language

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("i18n/setlang/", login_not_required(set_language), name="set_language"),
    path("", include("apps.core.urls")),
]
