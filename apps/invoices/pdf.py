"""Invoice PDF rendering with fpdf2 (pure Python, embedded Noto Sans).

Typst was the first choice (README §3) but its wheel weighs 84 MB, which
alone would break the image budget; fpdf2 + fontTools add about 12 MB
compressed.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.utils import formats, translation
from django.utils.translation import gettext as _
from fpdf import FPDF

from apps.invoices.models import Invoice, InvoiceSettings, Kind

FONTS = Path(__file__).resolve().parent / "fonts"
INK = (24, 24, 27)
MUTED = (113, 113, 122)
RULE = (228, 228, 231)

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF"}


def money(value: Decimal, currency: str) -> str:
    text = formats.number_format(value, decimal_pos=2, use_l10n=True, force_grouping=True)
    return f"{text} {CURRENCY_SYMBOLS.get(currency, currency)}"


def quantity(value: Decimal) -> str:
    return formats.number_format(
        value.normalize() if value == value.to_integral() else value, use_l10n=True
    )


class InvoicePDF(FPDF):
    def __init__(self, footer_text: str) -> None:
        super().__init__(format="A4", unit="mm")
        self.footer_text = footer_text
        self.add_font("Noto", "", str(FONTS / "NotoSans-Regular.ttf"))
        self.add_font("Noto", "B", str(FONTS / "NotoSans-Bold.ttf"))
        self.set_auto_page_break(auto=True, margin=25)
        self.set_margins(18, 18, 18)

    def footer(self) -> None:
        self.set_y(-18)
        self.set_font("Noto", "", 7)
        self.set_text_color(*MUTED)
        if self.footer_text:
            self.multi_cell(0, 3.5, self.footer_text, align="C")
        self.set_y(-10)
        self.cell(0, 4, f"{self.page_no()}/{{nb}}", align="R")


def render_invoice(invoice: Invoice, conf: InvoiceSettings) -> bytes:
    with translation.override(settings.LANGUAGE_CODE):
        return _render(invoice, conf)


def _render(invoice: Invoice, conf: InvoiceSettings) -> bytes:
    footer = "\n".join(t for t in (conf.legal_mentions, conf.footer) if t)
    pdf = InvoicePDF(footer)
    pdf.set_title(f"{invoice.number}")
    pdf.set_author(conf.issuer_name or invoice.space.name)
    pdf.set_creator("PFM-NG")
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin
    cur = invoice.currency

    # Issuer (left) and document title (right).
    top = pdf.get_y()
    pdf.set_text_color(*INK)
    pdf.set_font("Noto", "B", 12)
    pdf.multi_cell(
        width / 2,
        6,
        conf.issuer_name or invoice.space.name,
        align="L",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_font("Noto", "", 9)
    pdf.set_text_color(*MUTED)
    if conf.issuer_address:
        pdf.multi_cell(
            width / 2, 4.5, conf.issuer_address, align="L", new_x="LMARGIN", new_y="NEXT"
        )
    left_bottom = pdf.get_y()

    pdf.set_xy(pdf.l_margin + width / 2, top)
    pdf.set_text_color(*INK)
    pdf.set_font("Noto", "B", 18)
    title = {Kind.CREDIT: _("Credit note"), Kind.QUOTE: _("Quote")}.get(
        Kind(invoice.kind), _("Invoice")
    )
    pdf.cell(width / 2, 9, title, align="R", new_x="LEFT", new_y="NEXT")
    pdf.set_font("Noto", "", 9)
    meta = [
        (_("Number"), invoice.number or ""),
        (
            _("Date"),
            formats.date_format(invoice.issue_date or invoice.created_at, "SHORT_DATE_FORMAT"),
        ),
    ]
    if invoice.due_date and invoice.kind != Kind.CREDIT:
        label = _("Valid until") if invoice.kind == Kind.QUOTE else _("Due date")
        meta.append((label, formats.date_format(invoice.due_date, "SHORT_DATE_FORMAT")))
    if invoice.credited_invoice:
        meta.append((_("Cancels invoice"), invoice.credited_invoice.number or ""))
    if invoice.kind != Kind.QUOTE:
        meta.append(
            (
                _("Period"),
                f"{formats.date_format(invoice.period_start, 'SHORT_DATE_FORMAT')} – "
                f"{formats.date_format(invoice.period_end, 'SHORT_DATE_FORMAT')}",
            )
        )
    for label, value in meta:
        pdf.set_x(pdf.l_margin + width / 2)
        pdf.set_text_color(*MUTED)
        pdf.cell(width / 4, 5, label, align="R")
        pdf.set_text_color(*INK)
        pdf.cell(width / 4, 5, value, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(max(left_bottom, pdf.get_y()) + 10)

    # Client block.
    pdf.set_x(pdf.l_margin + width / 2)
    pdf.set_font("Noto", "", 8)
    pdf.set_text_color(*MUTED)
    pdf.cell(width / 2, 5, _("Billed to").upper(), new_x="LEFT", new_y="NEXT")
    pdf.set_text_color(*INK)
    pdf.set_font("Noto", "B", 10)
    pdf.multi_cell(width / 2, 5, invoice.client_name, new_x="LEFT", new_y="NEXT", align="L")
    pdf.set_font("Noto", "", 9)
    if invoice.client_address:
        pdf.multi_cell(
            width / 2, 4.5, invoice.client_address, new_x="LEFT", new_y="NEXT", align="L"
        )
    if invoice.client_reference:
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(
            width / 2,
            4.5,
            _("Reference: %(ref)s") % {"ref": invoice.client_reference},
            new_x="LEFT",
            new_y="NEXT",
            align="L",
        )
    pdf.set_x(pdf.l_margin)
    pdf.ln(8)

    # Lines.
    cols = [width - 80, 16, 14, 25, 25]
    headers = [_("Description"), _("Qty"), _("Unit"), _("Unit price"), _("Amount")]
    pdf.set_font("Noto", "B", 8)
    pdf.set_text_color(*MUTED)
    pdf.set_draw_color(*RULE)
    for w, h, align in zip(cols, headers, ["L", "R", "L", "R", "R"], strict=True):
        pdf.cell(w, 7, h.upper(), border="B", align=align)
    pdf.ln()
    pdf.set_font("Noto", "", 8.5)
    pdf.set_text_color(*INK)
    for line in invoice.lines.all():
        height = _row_height(pdf, cols[0], line.description)
        if pdf.get_y() + height > pdf.page_break_trigger:
            pdf.add_page()
        y = pdf.get_y()
        pdf.multi_cell(cols[0], 4.5, line.description, new_x="RIGHT", new_y="TOP", align="L")
        pdf.set_xy(pdf.l_margin + cols[0], y)
        pdf.cell(cols[1], 4.5, quantity(line.quantity), align="R")
        pdf.cell(cols[2], 4.5, f" {line.unit}")
        pdf.cell(cols[3], 4.5, money(line.unit_price, cur), align="R")
        pdf.cell(cols[4], 4.5, money(line.amount, cur), align="R")
        pdf.set_y(y + height + 1)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + width, pdf.get_y())
        pdf.ln(1.5)

    # Totals.
    pdf.ln(3)
    totals = [
        (_("Total excl. tax"), money(invoice.total_excl_tax, cur), False),
        (f"{_('VAT')} {quantity(invoice.vat_rate)} %", money(invoice.total_tax, cur), False),
        (_("Total incl. tax"), money(invoice.total_incl_tax, cur), True),
    ]
    for label, value, strong in totals:
        pdf.set_x(pdf.l_margin + width - 80)
        pdf.set_font("Noto", "B" if strong else "", 10 if strong else 9)
        pdf.cell(50, 6, label, align="R")
        pdf.cell(30, 6, value, align="R", new_x="LMARGIN", new_y="NEXT")
    if invoice.vat_rate == 0 and invoice.total_excl_tax:
        pdf.set_font("Noto", "", 8)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 5, _("VAT not applicable."), align="R", new_x="LMARGIN", new_y="NEXT")

    # Payment and notes.
    pdf.ln(8)
    pdf.set_text_color(*INK)
    if invoice.note:
        pdf.set_font("Noto", "", 9)
        pdf.multi_cell(width, 4.5, invoice.note, align="L")
        pdf.ln(3)
    if invoice.kind == Kind.INVOICE and conf.payment_details:
        pdf.set_font("Noto", "B", 9)
        pdf.cell(width, 5, _("Payment"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Noto", "", 9)
        pdf.multi_cell(width, 4.5, conf.payment_details, align="L")
    return bytes(pdf.output())


def _row_height(pdf: FPDF, width: float, text: str) -> float:
    lines = pdf.multi_cell(width, 4.5, text, dry_run=True, output="LINES", align="L")
    return max(len(lines) if isinstance(lines, list) else 1, 1) * 4.5
