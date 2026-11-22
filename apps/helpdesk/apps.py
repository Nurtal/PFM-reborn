from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class HelpdeskConfig(AppConfig):
    name = "apps.helpdesk"
    label = "helpdesk"
    verbose_name = _("Helpdesk")
