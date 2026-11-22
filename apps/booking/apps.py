from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class BookingConfig(AppConfig):
    name = "apps.booking"
    label = "booking"
    verbose_name = _("Booking")
