from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.utils import timezone
from ninja.security import HttpBearer

from apps.api.models import PREFIX, ApiToken, digest


class TokenAuth(HttpBearer):
    """Authorization: Bearer pfm_… (personal tokens; no cookies, so no CSRF)."""

    def authenticate(self, request: HttpRequest, token: str) -> Any:
        if not token.startswith(PREFIX):
            return None
        api_token = (
            ApiToken.objects.select_related("user")
            .filter(key_hash=digest(token), user__is_active=True)
            .first()
        )
        if api_token is None or api_token.is_expired:
            return None
        # Throttled write: at most one update per minute per token.
        now = timezone.now()
        if api_token.last_used_at is None or (now - api_token.last_used_at).total_seconds() > 60:
            ApiToken.objects.filter(pk=api_token.pk).update(last_used_at=now)
        request.user = api_token.user
        return api_token.user
