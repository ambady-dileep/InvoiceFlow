import csv
import logging

from django.db import DatabaseError
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.imports.models import Upload
from apps.imports.serializers import UploadSerializer, UploadStatusSerializer

logger = logging.getLogger(__name__)

FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r", "\n")


class UploadView(APIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        serializer = UploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save(merchant=request.user)
        except (DatabaseError, OSError):
            logger.exception("Could not store upload")
            return Response(
                {"detail": "The upload could not be saved. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)


class UploadStatusView(generics.RetrieveAPIView):
    serializer_class = UploadStatusSerializer

    def get_queryset(self):
        return Upload.objects.filter(merchant=self.request.user)


class UploadErrorReportView(APIView):
    def perform_content_negotiation(self, request, force=False):
        # The report is always CSV, so an Accept: text/csv request must not fail.
        return super().perform_content_negotiation(request, force=True)

    def get(self, request, pk):
        upload = get_object_or_404(Upload, pk=pk, merchant=request.user)
        response = StreamingHttpResponse(
            _error_report_lines(upload), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="upload-{upload.id}-errors.csv"'
        )
        return response


class _Echo:
    """Lets csv.writer return each line instead of writing it to a file."""

    def write(self, value):
        return value


def _safe_cell(value):
    """Keep spreadsheets from treating a cell as a formula."""
    return f"'{value}" if value.startswith(FORMULA_TRIGGERS) else value


def _error_report_lines(upload):
    writer = csv.writer(_Echo())
    # The byte order mark makes Excel read the file as UTF-8.
    yield "﻿" + writer.writerow(["row_number", "column", "message"])
    row_errors = upload.row_errors.order_by("row_number").values_list(
        "row_number", "errors"
    )
    for row_number, errors in row_errors.iterator():
        for column in sorted(errors):
            for message in errors[column]:
                yield writer.writerow([row_number, column, _safe_cell(message)])
