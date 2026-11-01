from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import models


def upload_file_path(instance, filename):
    """Build the stored path without using the client's filename."""
    extension = Path(filename).suffix.lower()
    return f"uploads/{instance.merchant_id}/{uuid4().hex}{extension}"


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
    file = models.FileField(upload_to=upload_file_path)
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
