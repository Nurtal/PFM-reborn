from __future__ import annotations

from allauth.account.adapter import DefaultAccountAdapter
from django.http import HttpRequest


class AccountAdapter(DefaultAccountAdapter):
    """Self sign-up is closed: accounts are created by space administrators.

    Joining requests (legacy "demandes d'adhésion") will reopen this per space.
    """

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return False
