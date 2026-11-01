from functools import partial

from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.invoices.models import InvoiceExport
from apps.invoices.serializers import InvoiceExportSerializer
from apps.invoices.tasks import queue_export
from apps.invoices.views import filtered_invoices


class InvoiceExportCreateView(APIView):
    def post(self, request):
        params = request.query_params.copy()
        params.pop("page", None)
        params.pop("page_size", None)
        query = params.urlencode()
        # Rejects invalid filters now, instead of in the background task.
        filtered_invoices(request.user.id, query)

        with transaction.atomic():
            export = InvoiceExport.objects.create(merchant=request.user, query=query)
            transaction.on_commit(partial(queue_export, export.id))
        return Response(
            InvoiceExportSerializer(export).data, status=status.HTTP_202_ACCEPTED
        )


class InvoiceExportDetailView(generics.RetrieveAPIView):
    serializer_class = InvoiceExportSerializer

    def get_queryset(self):
        return InvoiceExport.objects.filter(merchant=self.request.user)


class InvoiceExportDownloadView(APIView):
    def perform_content_negotiation(self, request, force=False):
        # The file is always CSV, so an Accept: text/csv request must not fail.
        return super().perform_content_negotiation(request, force=True)

    def get(self, request, pk):
        export = get_object_or_404(
            InvoiceExport,
            pk=pk,
            merchant=request.user,
            status=InvoiceExport.Status.COMPLETED,
        )
        try:
            file = export.file.open("rb")
        except FileNotFoundError:
            raise Http404
        return FileResponse(
            file,
            as_attachment=True,
            filename=f"invoices-{export.id}.csv",
            content_type="text/csv; charset=utf-8",
        )
