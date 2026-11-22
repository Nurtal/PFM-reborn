from __future__ import annotations

from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.http import HttpRequest


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """SSO users may always create their account: the institution vouches for
    them. They still get no access until a space administrator approves them."""

    def is_open_for_signup(self, request: HttpRequest, sociallogin: Any) -> bool:
        return True


class AccountAdapter(DefaultAccountAdapter):
    """Self sign-up follows the instance setting (closed by default).

    Signing up gives no access by itself: users then send a join request
    that a space administrator approves.
    """

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        from apps.core.models import InstanceSettings

        return InstanceSettings.load().allow_signup
