from django.conf import settings
from django.db import models


class Upload(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    # The composite index below also serves lookups by merchant.
    merchant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="uploads",
        db_index=False,
    )
    original_filename = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    total_rows = models.PositiveIntegerField(default=0)
    processed_rows = models.PositiveIntegerField(default=0)
    successful_rows = models.PositiveIntegerField(default=0)
    failed_rows = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # Target of the composite foreign key from Invoice.
            models.UniqueConstraint(
                fields=["id", "merchant"], name="upload_id_merchant_unique"
            ),
        ]
        indexes = [
            models.Index(
                fields=["merchant", "-created_at"], name="upload_merchant_created_idx"
            ),
        ]

    def __str__(self):
        return self.original_filename
