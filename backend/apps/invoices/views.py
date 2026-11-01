from django.core.paginator import Paginator
from django.http import HttpRequest, QueryDict
from django.utils.functional import cached_property
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, generics
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request

from apps.invoices.cache import cached_count
from apps.invoices.models import Invoice
from apps.invoices.serializers import InvoiceSerializer


class CachedCountPaginator(Paginator):
    def __init__(self, object_list, per_page, request):
        super().__init__(object_list, per_page)
        self.request = request

    @cached_property
    def count(self):
        return cached_count(
            self.request.user.id, self.request.query_params, self.object_list.count
        )


class InvoicePagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100

    def django_paginator_class(self, object_list, per_page):
        return CachedCountPaginator(object_list, per_page, self.request)


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


def filtered_invoices(merchant_id, query):
    """Apply the invoice list's filters, search, and ordering, without pagination."""
    request = HttpRequest()
    request.GET = QueryDict(query)
    view = InvoiceListView()
    view.request = Request(request)
    return view.filter_queryset(Invoice.objects.filter(merchant_id=merchant_id))
