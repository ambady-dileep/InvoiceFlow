from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class Invoice(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        OVERDUE = "overdue", "Overdue"

    # Stored directly because every listing filters by merchant. Migration 0002
    # adds a composite foreign key that keeps it equal to upload.merchant.
    # Both foreign keys are served by the composite indexes and constraints below.
    merchant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="invoices",
        db_index=False,
    )
    upload = models.ForeignKey(
        "imports.Upload",
        on_delete=models.CASCADE,
        related_name="invoices",
        db_index=False,
    )
    row_number = models.PositiveIntegerField()
    invoice_number = models.CharField(max_length=50)
    customer_name = models.CharField(max_length=255)
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    invoice_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["merchant", "invoice_number"],
                name="invoice_merchant_number_unique",
            ),
            # Lets a retried task recognise rows it has already stored.
            models.UniqueConstraint(
                fields=["upload", "row_number"], name="invoice_upload_row_unique"
            ),
            # Rows are saved with bulk_create, which skips model validation.
            models.CheckConstraint(
                condition=Q(amount__gte=0), name="invoice_amount_non_negative"
            ),
            models.CheckConstraint(
                condition=Q(status__in=["pending", "paid", "overdue"]),
                name="invoice_status_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["merchant", "-invoice_date"], name="invoice_merchant_date_idx"
            ),
            models.Index(
                fields=["merchant", "status", "-invoice_date"],
                name="invoice_merchant_status_idx",
            ),
            models.Index(
                fields=["merchant", "amount"], name="invoice_merchant_amount_idx"
            ),
        ]

    def __str__(self):
        return self.invoice_number
