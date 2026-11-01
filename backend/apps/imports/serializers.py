from django.db import DatabaseError
from rest_framework import serializers

from apps.imports.models import Upload
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
            upload.save()
        except DatabaseError:
            # The file is written before the row is inserted.
            upload.file.delete(save=False)
            raise
        return upload
