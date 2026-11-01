from django.urls import path

from apps.invoices.export_views import InvoiceExportCreateView
from apps.invoices.views import InvoiceListView

urlpatterns = [
    path("", InvoiceListView.as_view(), name="invoice-list"),
    path("export/", InvoiceExportCreateView.as_view(), name="invoice-export"),
]
