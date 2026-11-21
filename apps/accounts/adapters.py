from __future__ import annotations

from allauth.account.adapter import DefaultAccountAdapter
from django.http import HttpRequest


class AccountAdapter(DefaultAccountAdapter):
    """Self sign-up follows the instance setting (closed by default).

    Signing up gives no access by itself: users then send a join request
    that a space administrator approves.
    """

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        from apps.core.models import InstanceSettings

        return InstanceSettings.load().allow_signup
