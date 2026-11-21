from django.urls import path

from apps.invoices import views

app_name = "invoices"

urlpatterns = [
    path("", views.InvoiceListView.as_view(), name="list"),
    path("run/", views.InvoiceRunView.as_view(), name="run"),
    path("quotes/new/", views.QuoteCreateView.as_view(), name="quote_create"),
    path("settings/", views.InvoiceSettingsView.as_view(), name="settings"),
    path("export.<str:fmt>", views.InvoiceExportView.as_view(), name="export"),
    path("<uuid:invoice_id>/", views.InvoiceDetailView.as_view(), name="detail"),
    path("<uuid:invoice_id>/pdf/", views.InvoicePDFView.as_view(), name="pdf"),
]
