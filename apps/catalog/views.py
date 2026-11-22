"""Public, read-only catalogue of a platform (no login, opt-in per space)."""

from __future__ import annotations

from django.contrib.auth.decorators import login_not_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import cache_control

from apps.core.models import Space
from apps.resources.models import Resource
from apps.services.models import ServiceItem


@login_not_required
@cache_control(public=True, max_age=300)
def catalogue(request: HttpRequest, space_slug: str) -> HttpResponse:
    space = get_object_or_404(Space, slug=space_slug, is_active=True)
    if not space.catalogue_public:
        raise Http404
    # Only what a visitor may see: active resources outside restricted areas.
    resources = (
        Resource.objects.for_space(space)
        .filter(is_active=True, area__is_restricted=False)
        .select_related("category", "area")
        .order_by("category__name", "order", "name")
    )
    services = ServiceItem.objects.for_space(space).filter(is_active=True)
    request.space = space  # type: ignore[attr-defined]  # accent colour
    return render(
        request,
        "catalog/catalogue.html",
        {"space": space, "resources": resources, "services": services},
    )
