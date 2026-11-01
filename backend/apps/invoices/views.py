from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, generics
from rest_framework.pagination import PageNumberPagination

from apps.invoices.models import Invoice
from apps.invoices.serializers import InvoiceSerializer


class InvoicePagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class StableOrderingFilter(filters.OrderingFilter):
    """End every sort on a unique column, so rows cannot shift between pages."""

    def get_ordering(self, request, queryset, view):
        return [*super().get_ordering(request, queryset, view), "-id"]


class InvoiceListView(generics.ListAPIView):
    serializer_class = InvoiceSerializer
    pagination_class = InvoicePagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, StableOrderingFilter]
    filterset_fields = {
        "status": ["exact"],
        "invoice_date": ["gte", "lte"],
        "amount": ["gte", "lte"],
    }
    search_fields = ["invoice_number", "customer_name"]
    ordering_fields = [
        "invoice_number",
        "customer_name",
        "amount",
        "invoice_date",
        "status",
    ]
    ordering = ["-invoice_date"]

    def get_queryset(self):
        return Invoice.objects.filter(merchant=self.request.user)
