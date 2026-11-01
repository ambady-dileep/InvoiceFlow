from functools import partial

from django.db import DatabaseError, transaction
from rest_framework import serializers

from apps.imports.models import Upload
from apps.imports.tasks import queue_upload
from apps.imports.validators import validate_upload_file


class UploadSerializer(serializers.ModelSerializer):
    upload_id = serializers.IntegerField(source="id", read_only=True)
    file = serializers.FileField(write_only=True, validators=[validate_upload_file])

    class Meta:
        model = Upload
        fields = ["upload_id", "file", "status", "original_filename", "created_at"]
        read_only_fields = ["status", "original_filename", "created_at"]

    def create(self, validated_data):
        file = validated_data["file"]
        upload = Upload(
            merchant=validated_data["merchant"],
            original_filename=file.name,
            file=file,
        )
        try:
            with transaction.atomic():
                upload.save()
                transaction.on_commit(partial(queue_upload, upload.id))
        except DatabaseError:
            # The file is written before the row is inserted.
            upload.file.delete(save=False)
            raise
        return upload


class UploadStatusSerializer(serializers.ModelSerializer):
    upload_id = serializers.IntegerField(source="id")
    progress = serializers.SerializerMethodField()

    class Meta:
        model = Upload
        fields = [
            "upload_id",
            "status",
            "progress",
            "total_rows",
            "processed_rows",
            "successful_rows",
            "failed_rows",
            "failure_reason",
            "created_at",
        ]

    def get_progress(self, upload):
        """Return a whole percentage that reaches 100 only on completed uploads."""
        if upload.status == Upload.Status.COMPLETED:
            return 100
        if not upload.total_rows:
            return 0
        return min(upload.processed_rows * 100 // upload.total_rows, 99)
