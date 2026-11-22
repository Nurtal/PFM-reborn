from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal
from typing import Any, cast

from django.contrib import messages
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View
from django.views.generic import ListView, TemplateView, UpdateView

from apps.accounts.models import User
from apps.core.permissions import SpaceViewMixin
from apps.invoices import services
from apps.invoices.forms import InvoiceRunForm, InvoiceSettingsForm, LineForm
from apps.invoices.models import Invoice, InvoiceLine, InvoiceSettings, Status


class InvoicingMixin(SpaceViewMixin):
    permission_required = "core.manage_space"

    def actor(self) -> User:
        return cast(User, self.request.user)

    def get_invoice(self) -> Invoice:
        return get_object_or_404(
            Invoice.objects.for_space(self.space).select_related(
                "client", "credited_invoice", "issued_by"
            ),
            public_id=self.kwargs["invoice_id"],
        )


def _filtered(space: Any, params: Any) -> Any:
    qs = services.space_invoices(space)
    status = params.get("status", "issued")
    if status in {Status.DRAFT, Status.ISSUED}:
        qs = qs.filter(status=status)
    year = params.get("year", "")
    if year.isdigit():
        qs = qs.filter(issue_date__year=int(year))
    q = params.get("q", "").strip()
    if q:
        qs = qs.filter(number__icontains=q) | qs.filter(client__name__icontains=q)
    return qs.order_by("-issue_date", "-number", "-created_at")


class InvoiceListView(InvoicingMixin, ListView):
    template_name = "invoices/invoice_list.html"
    context_object_name = "invoices"
    paginate_by = 50

    def get_queryset(self) -> Any:
        return _filtered(self.space, self.request.GET)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx["status"] = self.request.GET.get("status", "issued")
        ctx["drafts_count"] = (
            Invoice.objects.for_space(self.space).filter(status=Status.DRAFT).count()
        )
        ctx["query"] = self.request.GET.urlencode()
        return ctx


class InvoiceRunView(InvoicingMixin, TemplateView):
    """Create drafts for one client or every client over a period."""

    template_name = "invoices/run.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = kwargs.get("form") or InvoiceRunForm(space=self.space)
        return ctx

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = InvoiceRunForm(request.POST, space=self.space)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        d = form.cleaned_data
        if d["client"]:
            results = [
                services.create_draft(
                    self.actor(), self.space, d["client"], d["period_start"], d["period_end"]
                )
            ]
        else:
            results = services.create_drafts_for_period(
                self.actor(), self.space, d["period_start"], d["period_end"]
            )
        created = [r.invoice for r in results if r.invoice]
        unpriced = sum(len(r.unpriced) for r in results)
        if created:
            messages.success(
                request,
                ngettext("%(n)d draft created.", "%(n)d drafts created.", len(created))
                % {"n": len(created)},
            )
        else:
            messages.info(request, _("Nothing to bill over this period."))
        if unpriced:
            messages.warning(
                request,
                ngettext(
                    "%(n)d booking or service has no price for its client and was left out.",
                    "%(n)d bookings or services have no price for their client and were left out.",
                    unpriced,
                )
                % {"n": unpriced},
            )
        if len(created) == 1:
            return redirect(
                "invoices:detail", space_slug=self.space.slug, invoice_id=created[0].public_id
            )
        return redirect(f"/s/{self.space.slug}/invoices/?status=draft")


class InvoiceDetailView(InvoicingMixin, TemplateView):
    template_name = "invoices/invoice_detail.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        invoice = kwargs.get("invoice") or self.get_invoice()
        conf = InvoiceSettings.for_space(self.space)
        lines = list(invoice.lines.all())
        totals = (
            services.compute_totals(
                [line.amount for line in lines], services.vat_rate_for(invoice, conf)
            )
            if invoice.status == Status.DRAFT
            else None
        )
        ctx.update(
            invoice=invoice,
            lines=lines,
            totals=totals,
            vat_rate=services.vat_rate_for(invoice, conf) if totals else invoice.vat_rate,
            line_form=kwargs.get("line_form") or LineForm(),
            credit_note=Invoice.objects.filter(credited_invoice=invoice).first(),
        )
        return ctx

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        invoice = self.get_invoice()
        action = request.POST.get("action")
        back = redirect("invoices:detail", space_slug=self.space.slug, invoice_id=invoice.public_id)
        try:
            if action == "add_line":
                form = LineForm(request.POST)
                if not form.is_valid():
                    return self.render_to_response(
                        self.get_context_data(invoice=invoice, line_form=form)
                    )
                d = form.cleaned_data
                services.add_line(
                    invoice, d["description"], d["quantity"], d["unit_price"], d["unit"]
                )
            elif action == "remove_line":
                line = get_object_or_404(InvoiceLine, invoice=invoice, pk=request.POST.get("line"))
                services.remove_line(line)
            elif action == "issue":
                invoice = services.issue(self.actor(), invoice)
                messages.success(request, _("Invoice %(n)s issued.") % {"n": invoice.number})
            elif action == "delete":
                services.delete_draft(invoice)
                messages.success(request, _("Draft deleted."))
                return redirect(f"/s/{self.space.slug}/invoices/?status=draft")
            elif action == "credit":
                note = services.credit(self.actor(), invoice, request.POST.get("reason", ""))
                messages.success(request, _("Credit note %(n)s issued.") % {"n": note.number})
                return redirect(
                    "invoices:detail", space_slug=self.space.slug, invoice_id=note.public_id
                )
            elif action == "paid":
                raw = request.POST.get("paid_at", "")
                services.mark_paid(invoice, dt.date.fromisoformat(raw) if raw else None)
                messages.success(request, _("Payment status saved."))
        except services.InvoicingError as exc:
            messages.error(request, exc.message)
        except ValueError:
            messages.error(request, _("Invalid date."))
        return back


class InvoicePDFView(InvoicingMixin, View):
    """Permission-checked download: media files are never served directly."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> FileResponse:
        invoice = self.get_invoice()
        if not invoice.pdf:
            raise Http404
        return FileResponse(
            invoice.pdf.open("rb"),
            content_type="application/pdf",
            as_attachment=bool(request.GET.get("download")),
            filename=f"{invoice.number}.pdf",
        )


EXPORT_HEADERS = [
    "number",
    "kind",
    "issue_date",
    "due_date",
    "client",
    "excl_tax",
    "vat_rate",
    "tax",
    "incl_tax",
    "paid_at",
]


def _export_rows(invoices: Any) -> list[list[Any]]:
    return [
        [
            i.number,
            i.get_kind_display(),
            i.issue_date,
            i.due_date,
            i.client_name or i.client.name,
            i.total_excl_tax,
            i.vat_rate,
            i.total_tax,
            i.total_incl_tax,
            i.paid_at,
        ]
        for i in invoices
    ]


class InvoiceExportView(InvoicingMixin, View):
    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        params = request.GET.copy()
        params["status"] = "issued"
        rows = _export_rows(_filtered(self.space, params))
        stamp = dt.date.today().isoformat()
        if kwargs["fmt"] == "xlsx":
            from openpyxl import Workbook

            wb = Workbook()
            ws = wb.active
            ws.title = "Invoices"
            ws.append(EXPORT_HEADERS)
            for row in rows:
                ws.append([float(v) if isinstance(v, Decimal) else v for v in row])
            buf = io.BytesIO()
            wb.save(buf)
            response = HttpResponse(
                buf.getvalue(),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            response["Content-Disposition"] = f'attachment; filename="invoices-{stamp}.xlsx"'
            return response
        text = io.StringIO()
        writer = csv.writer(text, delimiter=";")
        writer.writerow(EXPORT_HEADERS)
        for row in rows:
            writer.writerow(["" if v is None else _csv_safe(str(v)) for v in row])
        bom = chr(0xFEFF)  # lets Excel detect UTF-8
        response = HttpResponse(bom + text.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="invoices-{stamp}.csv"'
        return response


def _csv_safe(value: str) -> str:
    """Neutralise spreadsheet formulas (CSV injection) in user-provided text."""
    if value and value[0] in "=+-@\t\r" and not _is_number(value):
        return "'" + value
    return value


def _is_number(value: str) -> bool:
    try:
        Decimal(value)
    except ArithmeticError:
        return False
    return True


class InvoiceSettingsView(InvoicingMixin, UpdateView):
    form_class = InvoiceSettingsForm
    template_name = "resources/form.html"

    def get_object(self, queryset: Any = None) -> InvoiceSettings:
        return InvoiceSettings.for_space(self.space)

    def get_success_url(self) -> str:
        return f"/s/{self.space.slug}/invoices/"

    def form_valid(self, form: Any) -> HttpResponse:
        messages.success(self.request, _("Saved."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = _("Invoicing settings")
        ctx["cancel_url"] = self.get_success_url()
        return ctx
