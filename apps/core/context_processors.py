from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.utils.functional import SimpleLazyObject

from apps.core import services
from apps.core.models import InstanceSettings, Space


def pfm(request: HttpRequest) -> dict[str, Any]:
    space: Space | None = getattr(request, "space", None)
    user = request.user
    can_administer = bool(space and user.has_perm("core.administer_space", space))
    can_manage = bool(space and user.has_perm("core.manage_space", space))
    return {
        "current_space": space,
        "can_administer_space": can_administer,
        "can_manage_space": can_manage,
        # Lazy: only queried by templates that render the space switcher.
        "signup_open": SimpleLazyObject(lambda: InstanceSettings.load().allow_signup),
        "user_spaces": SimpleLazyObject(
            lambda: services.spaces_for(user) if user.is_authenticated else []  # type: ignore[arg-type]
        ),
    }
