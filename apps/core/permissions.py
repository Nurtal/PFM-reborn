"""View helpers enforcing space scoping and permissions (deny by default)."""

from __future__ import annotations

from typing import Any

from django.http import Http404, HttpRequest, HttpResponseBase
from django.shortcuts import get_object_or_404
from django.views import View

from apps.core.models import Space


class SpaceViewMixin(View):
    """Resolve ``request.space`` from the ``space_slug`` URL kwarg and check access.

    Each subclass declares ``permission_required``. A user without the right
    gets a 404 (not 403) so the existence of other spaces is not leaked.
    """

    permission_required: str = ""
    space: Space

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if not self.permission_required:
            raise NotImplementedError(f"{type(self).__name__} must set permission_required")
        self.space = get_object_or_404(Space, slug=kwargs["space_slug"])
        if not request.user.has_perm(self.permission_required, self.space):
            raise Http404
        request.space = self.space  # type: ignore[attr-defined]
        return super().dispatch(request, *args, **kwargs)
