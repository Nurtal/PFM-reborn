from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib import messages
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from apps.clients import forms
from apps.clients.models import Client, Package, PackagePrice, PricingGrid, ResourcePrice
from apps.core.permissions import SpaceViewMixin
from apps.resources.models import Resource
from apps.resources.views import SpaceFormMixin


class ClientListView(SpaceViewMixin, ListView):
    permission_required = "core.manage_space"
    template_name = "clients/client_list.html"
    context_object_name = "clients"
    paginate_by = 50

    def get_queryset(self) -> QuerySet[Client]:
        qs = Client.objects.for_space(self.space).select_related("pricing_grid")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(name__icontains=q) | qs.filter(organization__icontains=q)
        return qs.order_by("name")

    def get_template_names(self) -> list[str]:
        if self.request.htmx:  # type: ignore[attr-defined]
            return ["clients/_client_rows.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx["grids"] = PricingGrid.objects.for_space(self.space)
        return ctx


class ClientCreateView(SpaceFormMixin, CreateView):
    model = Client
    form_class = forms.ClientForm
    list_url_name = "clients:list"
    title = gettext_lazy("New client")


class ClientUpdateView(SpaceFormMixin, UpdateView):
    model = Client
    form_class = forms.ClientForm
    list_url_name = "clients:list"
    title = gettext_lazy("Edit client")


class GridCreateView(SpaceFormMixin, CreateView):
    model = PricingGrid
    form_class = forms.PricingGridForm
    list_url_name = "clients:prices"
    title = gettext_lazy("New pricing grid")


class GridUpdateView(SpaceFormMixin, UpdateView):
    model = PricingGrid
    form_class = forms.PricingGridForm
    list_url_name = "clients:prices"
    title = gettext_lazy("Edit pricing grid")


class PackageCreateView(SpaceFormMixin, CreateView):
    model = Package
    form_class = forms.PackageForm
    list_url_name = "clients:prices"
    title = gettext_lazy("New package")


class PackageUpdateView(SpaceFormMixin, UpdateView):
    model = Package
    form_class = forms.PackageForm
    list_url_name = "clients:prices"
    title = gettext_lazy("Edit package")


def _decimal(raw: str) -> Decimal | None:
    raw = raw.strip().replace(",", ".")
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError(raw) from None
    if value < 0 or value.as_tuple().exponent < -2:  # type: ignore[operator]
        raise ValueError(raw)
    return value


class PriceMatrixView(SpaceViewMixin, TemplateView):
    """All hourly rates and package prices of the space in one editable table."""

    permission_required = "core.manage_space"
    template_name = "clients/prices.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        grids = list(PricingGrid.objects.for_space(self.space))
        resources = list(Resource.objects.for_space(self.space).filter(is_active=True))
        packages = list(
            Package.objects.for_space(self.space).select_related("resource").filter(is_active=True)
        )
        prices = {
            (p.resource_id, p.grid_id): p for p in ResourcePrice.objects.for_space(self.space)
        }
        package_prices = {
            (p.package_id, p.grid_id): p for p in PackagePrice.objects.for_space(self.space)
        }
        from apps.services.models import ServiceItem, ServicePrice

        items = list(ServiceItem.objects.for_space(self.space).filter(is_active=True))
        service_prices = {
            (p.item_id, p.grid_id): p for p in ServicePrice.objects.for_space(self.space)
        }
        ctx["service_rows"] = [
            (item, [(g, service_prices.get((item.pk, g.pk))) for g in grids]) for item in items
        ]
        ctx.update(
            grids=grids,
            rows=[(r, [(g, prices.get((r.pk, g.pk))) for g in grids]) for r in resources],
            package_rows=[
                (pk, [(g, package_prices.get((pk.pk, g.pk))) for g in grids]) for pk in packages
            ],
            errors=kwargs.get("errors", []),
        )
        return ctx

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        try:
            with transaction.atomic():
                errors = self._save(request.POST)
                if errors:
                    raise _InvalidAmounts(errors)
        except _InvalidAmounts as exc:
            messages.error(
                request,
                _("Invalid amounts: %(values)s") % {"values": ", ".join(exc.values[:5])},
            )
            return self.render_to_response(self.get_context_data(errors=exc.values))
        messages.success(request, _("Prices saved."))
        return redirect("clients:prices", space_slug=self.space.slug)

    def _save(self, data: Any) -> list[str]:
        grids = {g.pk: g for g in PricingGrid.objects.for_space(self.space)}
        resources = {r.pk: r for r in Resource.objects.for_space(self.space)}
        packages = {p.pk: p for p in Package.objects.for_space(self.space)}
        from apps.services.models import ServiceItem

        items = {i.pk: i for i in ServiceItem.objects.for_space(self.space)}
        errors: list[str] = []
        for key, raw in data.items():
            parts = key.split("-")
            try:
                if parts[0] == "rate" and len(parts) == 4 and parts[3] in RATE_FIELDS:
                    resource, grid = resources[int(parts[1])], grids[int(parts[2])]
                    self._save_rate(resource, grid, parts[3], _decimal(raw))
                elif parts[0] == "service" and len(parts) == 3:
                    item, grid = items[int(parts[1])], grids[int(parts[2])]
                    self._save_service(item, grid, _decimal(raw))
                elif parts[0] == "package" and len(parts) == 3:
                    package, grid = packages[int(parts[1])], grids[int(parts[2])]
                    self._save_package(package, grid, _decimal(raw))
            except (KeyError, IndexError):
                continue  # an id from another space, or garbage: ignore it
            except ValueError:
                errors.append(raw)
        return errors

    def _save_rate(
        self, resource: Resource, grid: PricingGrid, field: str, value: Decimal | None
    ) -> None:
        price = ResourcePrice.objects.filter(resource=resource, grid=grid).first()
        if field == "day" and value is None:
            if price is not None:
                price.delete()
            return
        if price is None:
            if field != "day":
                return  # a night/weekend rate needs a day rate (posted first)
            price = ResourcePrice(space=self.space, resource=resource, grid=grid)
        setattr(price, f"{field}_rate", value)
        price.save()

    def _save_service(self, item: Any, grid: PricingGrid, value: Decimal | None) -> None:
        from apps.services.models import ServicePrice

        if value is None:
            ServicePrice.objects.filter(item=item, grid=grid).delete()
        else:
            ServicePrice.objects.update_or_create(
                item=item, grid=grid, defaults={"price": value, "space": self.space}
            )

    def _save_package(self, package: Package, grid: PricingGrid, value: Decimal | None) -> None:
        if value is None:
            PackagePrice.objects.filter(package=package, grid=grid).delete()
        else:
            PackagePrice.objects.update_or_create(
                package=package, grid=grid, defaults={"price": value, "space": self.space}
            )


RATE_FIELDS = {"day", "night", "weekend"}


class _InvalidAmounts(Exception):
    def __init__(self, values: list[str]) -> None:
        super().__init__(values)
        self.values = values
