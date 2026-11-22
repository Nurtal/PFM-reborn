from __future__ import annotations

import datetime as dt
from typing import Any

from django.contrib import messages
from django.db.models import Count, QuerySet, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from apps.core.permissions import SpaceViewMixin
from apps.resources.views import SpaceFormMixin
from apps.services import gantt, services
from apps.services.forms import (
    CabinetForm,
    OrderForm,
    OrderLineForm,
    ProjectForm,
    ServiceItemForm,
    ShelfForm,
    StockEntryForm,
)
from apps.services.models import (
    Cabinet,
    Order,
    OrderLine,
    OrderStatus,
    Project,
    ProjectStatus,
    ServiceItem,
    Shelf,
    StockEntry,
)


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


# --- Projects -------------------------------------------------------------------


class ProjectListView(SpaceViewMixin, TemplateView):
    """Projects with a Gantt chart over a sliding window of months."""

    permission_required = "core.manage_space"
    template_name = "services/project_list.html"
    months = 6

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        try:
            offset = int(self.request.GET.get("offset", "0"))
        except ValueError:
            offset = 0
        offset = max(-120, min(120, offset))
        # Start one month back so projects already under way show their beginning.
        month = today.month - 2 + offset
        start = dt.date(today.year + month // 12, month % 12 + 1, 1)
        projects = Project.objects.for_space(self.space).select_related("client", "lead")
        if not self.request.GET.get("closed"):
            projects = projects.exclude(status=ProjectStatus.CLOSED)
        chart = gantt.layout(list(projects), start, self.months, today)
        ctx.update(
            chart=chart,
            rows=len(chart.bars),
            projects=projects,
            offset=offset,
            prev_offset=offset - self.months,
            next_offset=offset + self.months,
            show_closed=bool(self.request.GET.get("closed")),
        )
        return ctx


class ProjectCreateView(SpaceFormMixin, CreateView):
    model = Project
    form_class = ProjectForm
    list_url_name = "services:projects"
    title = gettext_lazy("New project")


class ProjectUpdateView(SpaceFormMixin, UpdateView):
    model = Project
    form_class = ProjectForm
    list_url_name = "services:projects"
    title = gettext_lazy("Edit project")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        project = self.object
        ctx["orders"] = project.orders.select_related("client")
        ctx["total"], ctx["unpriced"] = services.project_total(project)
        return ctx

    def get_template_names(self) -> list[str]:
        return ["services/project_form.html"]


# --- Stock ------------------------------------------------------------------------


class StockView(SpaceViewMixin, TemplateView):
    permission_required = "core.manage_space"
    template_name = "services/stock.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        cabinets = Cabinet.objects.for_space(self.space).prefetch_related("shelves")
        entries = (
            StockEntry.objects.for_space(self.space)
            .filter(removed_on__isnull=True)
            .select_related("shelf__cabinet", "client")
        )
        q = self.request.GET.get("q", "").strip()
        if q:
            entries = entries.filter(label__icontains=q) | entries.filter(client__name__icontains=q)
        loads = {
            row["shelf"]: row["n"]
            for row in StockEntry.objects.for_space(self.space)
            .filter(removed_on__isnull=True)
            .values("shelf")
            .annotate(n=Sum("quantity"))
        }
        ctx.update(
            cabinets=[(c, [(s, loads.get(s.pk, 0)) for s in c.shelves.all()]) for c in cabinets],
            entries=entries,
            entry_form=kwargs.get("entry_form") or StockEntryForm(space=self.space),
            cabinet_form=kwargs.get("cabinet_form") or CabinetForm(space=self.space),
            shelf_form=kwargs.get("shelf_form") or ShelfForm(space=self.space),
        )
        return ctx

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        action = request.POST.get("action")
        back = redirect("services:stock", space_slug=self.space.slug)
        try:
            if action == "cabinet":
                cabinet_form = CabinetForm(request.POST, space=self.space)
                if not cabinet_form.is_valid():
                    return self.render_to_response(self.get_context_data(cabinet_form=cabinet_form))
                cabinet_form.save()
            elif action == "shelf":
                shelf_form = ShelfForm(request.POST, space=self.space)
                if not shelf_form.is_valid():
                    return self.render_to_response(self.get_context_data(shelf_form=shelf_form))
                Shelf.objects.create(**shelf_form.cleaned_data)
            elif action == "store":
                entry_form = StockEntryForm(request.POST, space=self.space)
                if not entry_form.is_valid():
                    return self.render_to_response(self.get_context_data(entry_form=entry_form))
                services.store(**entry_form.cleaned_data)
            elif action == "withdraw":
                entry = get_object_or_404(
                    StockEntry.objects.for_space(self.space), pk=request.POST.get("entry")
                )
                services.withdraw(entry)
            else:
                return back
            messages.success(request, _("Saved."))
        except services.OrderError as exc:
            messages.error(request, exc.message)
        return back
