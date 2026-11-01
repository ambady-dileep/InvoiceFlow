import logging

from django.db import DatabaseError
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.imports.serializers import UploadSerializer

logger = logging.getLogger(__name__)


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
