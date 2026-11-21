"""Object permissions (django-rules). Autodiscovered at startup.

Every predicate receives a ``Space``. Superusers pass everything through
Django's permission system; space roles are ordered (see ``Role``).
"""

from __future__ import annotations

import rules

from apps.core.models import Role, Space, user_role


def _has_role(minimum: Role) -> rules.Predicate:
    @rules.predicate(name=f"has_role_{minimum.name.lower()}")
    def predicate(user, space: Space | None) -> bool:  # type: ignore[no-untyped-def]
        if space is None or not user.is_authenticated:
            return False
        role = user_role(user, space)
        return role is not None and role >= minimum and space.is_active

    return predicate


is_superuser = rules.is_superuser
is_space_visitor = _has_role(Role.VISITOR)
is_space_user = _has_role(Role.USER)
is_space_manager = _has_role(Role.MANAGER)
is_space_admin = _has_role(Role.ADMIN)

rules.add_perm("core.view_space", is_superuser | is_space_visitor)
rules.add_perm("core.manage_space", is_superuser | is_space_manager)
rules.add_perm("core.administer_space", is_superuser | is_space_admin)
