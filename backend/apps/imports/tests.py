from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.imports.models import Upload

User = get_user_model()


class UploadModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.merchant = User.objects.create_user("merchant", "m@example.com", "pass")

    def test_create_uses_defaults(self):
        upload = Upload.objects.create(
            merchant=self.merchant, original_filename="invoices.csv"
        )

        upload.refresh_from_db()
        self.assertEqual(upload.status, Upload.Status.PENDING)
        self.assertEqual(upload.total_rows, 0)
        self.assertEqual(upload.processed_rows, 0)
        self.assertEqual(upload.successful_rows, 0)
        self.assertEqual(upload.failed_rows, 0)
        self.assertIsNotNone(upload.created_at)
        self.assertIn(upload, self.merchant.uploads.all())

    def test_updated_at_changes_on_save(self):
        upload = Upload.objects.create(
            merchant=self.merchant, original_filename="invoices.csv"
        )
        created = upload.updated_at

        upload.status = Upload.Status.PROCESSING
        upload.save()

        self.assertGreater(upload.updated_at, created)

    def test_valid_statuses_are_accepted(self):
        for status in Upload.Status.values:
            with self.subTest(status=status):
                upload = Upload(
                    merchant=self.merchant,
                    original_filename="invoices.csv",
                    status=status,
                )

                upload.full_clean()

    def test_invalid_status_is_rejected(self):
        upload = Upload(
            merchant=self.merchant, original_filename="invoices.csv", status="done"
        )

        with self.assertRaises(ValidationError) as context:
            upload.full_clean()

        self.assertIn("status", context.exception.message_dict)

    def test_required_fields_are_validated(self):
        with self.assertRaises(ValidationError) as context:
            Upload().full_clean()

        self.assertIn("merchant", context.exception.message_dict)
        self.assertIn("original_filename", context.exception.message_dict)

    def test_deleting_merchant_deletes_uploads(self):
        Upload.objects.create(merchant=self.merchant, original_filename="invoices.csv")

        User.objects.filter(pk=self.merchant.pk).delete()

        self.assertFalse(Upload.objects.exists())
