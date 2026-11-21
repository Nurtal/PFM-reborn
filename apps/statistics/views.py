from __future__ import annotations

import csv
import datetime as dt
import io
from typing import Any

from django import forms
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from apps.core.permissions import SpaceViewMixin
from apps.statistics import services

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
MAX_DAYS = 366 * 3


class PeriodForm(forms.Form):
    start = forms.DateField(label=_("From"), widget=DATE)
    end = forms.DateField(label=_("To"), widget=DATE)

    def clean(self) -> dict[str, Any]:
        super().clean()
        d = self.cleaned_data
        if d.get("start") and d.get("end"):
            if d["end"] < d["start"]:
                self.add_error("end", _("The period end must be after its start."))
            elif (d["end"] - d["start"]).days > MAX_DAYS:
                self.add_error("end", _("The period is limited to three years."))
        return d


def _period(request: HttpRequest) -> tuple[PeriodForm, dt.date, dt.date]:
    today = timezone.localdate()
    default = {"start": today.replace(month=1, day=1), "end": today}
    form = PeriodForm(request.GET or None, initial=default)
    if form.is_bound and form.is_valid():
        return form, form.cleaned_data["start"], form.cleaned_data["end"]
    return form, default["start"], default["end"]


class StatisticsView(SpaceViewMixin, TemplateView):
    permission_required = "core.manage_space"
    template_name = "statistics/dashboard.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        form, start, end = _period(self.request)
        usage = services.resource_usage(self.space, start, end)
        clients = services.client_usage(self.space, start, end)
        revenue = services.monthly_revenue(self.space, start, end)
        ctx.update(
            form=form,
            start=start,
            end=end,
            usage=usage,
            clients=clients[:15],
            revenue=revenue,
            total_hours=sum((u.hours for u in usage), start=0),
            total_bookings=sum(u.bookings for u in usage),
            total_revenue=sum((r for _m, r in revenue), start=0),
            charts={
                "usage": {
                    "labels": [u.resource.name for u in usage],
                    "values": [float(u.hours) for u in usage],
                },
                "revenue": {
                    "labels": [m.strftime("%Y-%m") for m, _r in revenue],
                    "values": [float(r) for _m, r in revenue],
                },
            },
        )
        return ctx


class StatisticsExportView(SpaceViewMixin, View):
    permission_required = "core.manage_space"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        _form, start, end = _period(request)
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";")
        table = kwargs["table"]
        if table == "resources":
            writer.writerow(["resource", "bookings", "hours", "open_hours", "occupancy_percent"])
            for u in services.resource_usage(self.space, start, end):
                writer.writerow(
                    [u.resource.name, u.bookings, u.hours, u.open_hours, u.occupancy or ""]
                )
        elif table == "clients":
            writer.writerow(["client", "bookings", "hours"])
            for c in services.client_usage(self.space, start, end):
                writer.writerow([c.name, c.bookings, c.hours])
        else:
            writer.writerow(["month", "revenue_excl_tax"])
            for month, total in services.monthly_revenue(self.space, start, end):
                writer.writerow([month.strftime("%Y-%m"), total])
        response = HttpResponse(
            chr(0xFEFF) + buf.getvalue(), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = f'attachment; filename="{table}-{start}-{end}.csv"'
        return response
