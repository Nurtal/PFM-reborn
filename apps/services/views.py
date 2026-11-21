from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.db.models import Count, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from apps.core.permissions import SpaceViewMixin
from apps.resources.views import SpaceFormMixin
from apps.services import services
from apps.services.forms import OrderForm, OrderLineForm, ServiceItemForm
from apps.services.models import Order, OrderLine, OrderStatus, ServiceItem


class CatalogueView(SpaceViewMixin, ListView):
    permission_required = "core.manage_space"
    template_name = "services/catalogue.html"
    context_object_name = "items"

    def get_queryset(self) -> QuerySet[ServiceItem]:
        return ServiceItem.objects.for_space(self.space)


class ServiceCreateView(SpaceFormMixin, CreateView):
    model = ServiceItem
    form_class = ServiceItemForm
    list_url_name = "services:catalogue"
    title = gettext_lazy("New service")


class ServiceUpdateView(SpaceFormMixin, UpdateView):
    model = ServiceItem
    form_class = ServiceItemForm
    list_url_name = "services:catalogue"
    title = gettext_lazy("Edit service")


class OrderListView(SpaceViewMixin, ListView):
    permission_required = "core.manage_space"
    template_name = "services/order_list.html"
    context_object_name = "orders"
    paginate_by = 50

    def get_queryset(self) -> QuerySet[Order]:
        status = self.request.GET.get("status", OrderStatus.OPEN)
        qs = (
            Order.objects.for_space(self.space)
            .select_related("client", "requester")
            .annotate(line_count=Count("lines"))
        )
        if status in OrderStatus.values:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx["status"] = self.request.GET.get("status", OrderStatus.OPEN)
        ctx["statuses"] = OrderStatus.choices
        return ctx


class OrderCreateView(SpaceFormMixin, CreateView):
    model = Order
    form_class = OrderForm
    list_url_name = "services:orders"
    title = gettext_lazy("New order")

    def get_success_url(self) -> str:
        return f"/s/{self.space.slug}/services/orders/{self.object.public_id}/"  # type: ignore[union-attr]


class OrderDetailView(SpaceViewMixin, TemplateView):
    permission_required = "core.manage_space"
    template_name = "services/order_detail.html"

    def get_order(self) -> Order:
        return get_object_or_404(
            Order.objects.for_space(self.space).select_related("client__pricing_grid", "requester"),
            public_id=self.kwargs["order_id"],
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        order = kwargs.get("order") or self.get_order()
        lines = list(order.lines.select_related("item"))
        priced = [(line, services.line_amount(line)) for line in lines]
        ctx.update(
            order=order,
            lines=priced,
            total=sum((a for _line, a in priced if a is not None), start=0),
            line_form=kwargs.get("line_form") or OrderLineForm(space=self.space),
            statuses=OrderStatus.choices,
        )
        return ctx

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        order = self.get_order()
        action = request.POST.get("action")
        try:
            if action == "add_line":
                form = OrderLineForm(request.POST, space=self.space)
                if not form.is_valid():
                    return self.render_to_response(
                        self.get_context_data(order=order, line_form=form)
                    )
                d = form.cleaned_data
                services.add_line(order, d["item"], d["quantity"], d["performed_on"], d["comment"])
                messages.success(request, _("Line added."))
            elif action == "remove_line":
                services.remove_line(
                    get_object_or_404(OrderLine, order=order, pk=request.POST.get("line"))
                )
                messages.success(request, _("Line removed."))
            elif action == "status":
                services.set_status(order, request.POST.get("status", ""))
                messages.success(request, _("Order updated."))
        except services.OrderError as exc:
            messages.error(request, exc.message)
        return redirect(
            "services:order_detail", space_slug=self.space.slug, order_id=order.public_id
        )
