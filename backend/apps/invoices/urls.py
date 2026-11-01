from django.urls import path

from apps.invoices.views import InvoiceListView

urlpatterns = [
    path("", InvoiceListView.as_view(), name="invoice-list"),
]
