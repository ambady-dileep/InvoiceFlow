from django.urls import reverse
from rest_framework import serializers

from apps.invoices.models import Invoice, InvoiceExport


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = [
            "id",
            "invoice_number",
            "customer_name",
            "amount",
            "invoice_date",
            "status",
        ]
        read_only_fields = fields


class InvoiceExportSerializer(serializers.ModelSerializer):
    export_id = serializers.IntegerField(source="id")
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = InvoiceExport
        fields = [
            "export_id",
            "status",
            "row_count",
            "failure_reason",
            "download_url",
            "created_at",
        ]

    def get_download_url(self, export):
        """Return the authenticated download path, never a public file address."""
        if export.status != InvoiceExport.Status.COMPLETED:
            return None
        return reverse("invoice-export-download", args=[export.id])
