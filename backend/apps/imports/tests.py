import csv
import io
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.db.models.sql.compiler import SQLInsertCompiler
from django.http import StreamingHttpResponse
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.imports.models import Upload, UploadRowError

User = get_user_model()

CSV_CONTENT = (
    b"invoice_number,customer_name,amount,invoice_date,status\n"
    b"INV-001,Acme Traders,1250.50,2026-01-15,pending\n"
)
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def xlsx_content(names=("[Content_Types].xml", "xl/workbook.xml")):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "<xml/>")
    return buffer.getvalue()


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
                    file="uploads/1/invoices.csv",
                    original_filename="invoices.csv",
                    status=status,
                )

                upload.full_clean()

    def test_invalid_status_is_rejected(self):
        upload = Upload(
            merchant=self.merchant,
            file="uploads/1/invoices.csv",
            original_filename="invoices.csv",
            status="done",
        )

        with self.assertRaises(ValidationError) as context:
            upload.full_clean()

        self.assertIn("status", context.exception.message_dict)

    def test_required_fields_are_validated(self):
        with self.assertRaises(ValidationError) as context:
            Upload().full_clean()

        self.assertIn("merchant", context.exception.message_dict)
        self.assertIn("file", context.exception.message_dict)
        self.assertIn("original_filename", context.exception.message_dict)

    def test_deleting_merchant_deletes_uploads(self):
        Upload.objects.create(merchant=self.merchant, original_filename="invoices.csv")

        User.objects.filter(pk=self.merchant.pk).delete()

        self.assertFalse(Upload.objects.exists())


class UploadApiTests(APITestCase):
    url = reverse("upload")

    @classmethod
    def setUpTestData(cls):
        cls.merchant = User.objects.create_user("merchant", "m@example.com", "pass")
        cls.other_merchant = User.objects.create_user("other", "o@example.com", "pass")

    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media_root = Path(media.name)
        settings_override = override_settings(MEDIA_ROOT=media.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.client.force_authenticate(self.merchant)

    def post_file(self, name, content, content_type="text/csv", **data):
        file = SimpleUploadedFile(name, content, content_type=content_type)
        return self.client.post(self.url, {"file": file, **data}, format="multipart")

    def stored_files(self):
        return [path for path in self.media_root.rglob("*") if path.is_file()]

    def assertRejected(self, response):
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("file", response.data)
        self.assertFalse(Upload.objects.exists())
        self.assertEqual(self.stored_files(), [])

    def test_csv_upload_is_accepted(self):
        response = self.post_file("invoices.csv", CSV_CONTENT)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        upload = Upload.objects.get()
        self.assertEqual(
            set(response.data),
            {"upload_id", "status", "original_filename", "created_at"},
        )
        self.assertEqual(response.data["upload_id"], upload.id)
        self.assertEqual(response.data["status"], Upload.Status.PENDING)
        self.assertEqual(response.data["original_filename"], "invoices.csv")
        self.assertEqual(upload.status, Upload.Status.PENDING)

    def test_xlsx_upload_is_accepted(self):
        response = self.post_file("invoices.xlsx", xlsx_content(), XLSX_TYPE)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(Upload.objects.get().original_filename, "invoices.xlsx")

    def test_file_is_stored_under_a_generated_name(self):
        self.post_file("invoices.csv", CSV_CONTENT)

        upload = Upload.objects.get()
        self.assertRegex(
            upload.file.name, rf"^uploads/{self.merchant.id}/[0-9a-f]{{32}}\.csv$"
        )
        self.assertEqual(self.stored_files(), [self.media_root / upload.file.name])
        with upload.file.open("rb") as stored:
            self.assertEqual(stored.read(), CSV_CONTENT)

    def test_filename_extension_is_case_insensitive(self):
        response = self.post_file("INVOICES.CSV", CSV_CONTENT)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(Upload.objects.get().file.name.endswith(".csv"))

    def test_merchant_comes_from_the_request_user(self):
        response = self.post_file(
            "invoices.csv",
            CSV_CONTENT,
            merchant=self.other_merchant.id,
            status="completed",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        upload = Upload.objects.get()
        self.assertEqual(upload.merchant, self.merchant)
        self.assertEqual(upload.status, Upload.Status.PENDING)

    def test_unauthenticated_request_is_rejected(self):
        self.client.force_authenticate(None)

        response = self.post_file("invoices.csv", CSV_CONTENT)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(Upload.objects.exists())

    def test_missing_file_is_rejected(self):
        self.assertRejected(self.client.post(self.url, {}, format="multipart"))

    def test_text_instead_of_file_is_rejected(self):
        self.assertRejected(
            self.client.post(self.url, {"file": "text"}, format="multipart")
        )

    def test_empty_file_is_rejected(self):
        self.assertRejected(self.post_file("invoices.csv", b""))

    def test_non_multipart_request_is_rejected(self):
        response = self.client.post(self.url, {"file": "text"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

    def test_unsupported_extensions_are_rejected(self):
        for name in ("invoices.txt", "invoices.xls", "invoices.csv.exe", "invoices"):
            with self.subTest(name=name):
                self.assertRejected(self.post_file(name, CSV_CONTENT))

    def test_mismatched_content_type_is_rejected(self):
        cases = [
            ("invoices.csv", CSV_CONTENT, "image/png"),
            ("invoices.xlsx", xlsx_content(), "text/csv"),
        ]
        for name, content, content_type in cases:
            with self.subTest(name=name):
                self.assertRejected(self.post_file(name, content, content_type))

    def test_invalid_csv_content_is_rejected(self):
        header = CSV_CONTENT.splitlines()[0]
        cases = [
            b"\xff\xfe\x00not utf-8",
            b"PK\x03\x04\x00\x00binary",
            b"<html><body>not a csv</body></html>",
            b"\n\n",
            b"invoice_number,customer_name\nINV-001,Acme\n",
            header,
        ]
        for content in cases:
            with self.subTest(content=content):
                self.assertRejected(self.post_file("invoices.csv", content))

    def test_missing_columns_are_named(self):
        response = self.post_file("invoices.csv", b"invoice_number,amount\nINV-1,5\n")

        self.assertEqual(
            response.data["file"],
            ["Missing required columns: customer_name, invoice_date, status."],
        )

    def test_csv_header_variations_are_accepted(self):
        cases = [
            b"\xef\xbb\xbf" + CSV_CONTENT,
            CSV_CONTENT.replace(b"invoice_number,", b" Invoice_Number ,"),
            CSV_CONTENT.replace(b",status\n", b",status,notes\n"),
            CSV_CONTENT.replace(b"\n", b"\r\n"),
        ]
        for content in cases:
            with self.subTest(content=content):
                response = self.post_file("invoices.csv", content)

                self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

    def test_invalid_xlsx_content_is_rejected(self):
        cases = [CSV_CONTENT, xlsx_content(names=("readme.txt",))]
        for content in cases:
            with self.subTest(content=content):
                self.assertRejected(
                    self.post_file("invoices.xlsx", content, XLSX_TYPE)
                )

    @override_settings(MAX_UPLOAD_SIZE=len(CSV_CONTENT))
    def test_file_at_the_size_limit_is_accepted(self):
        response = self.post_file("invoices.csv", CSV_CONTENT)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

    @override_settings(MAX_UPLOAD_SIZE=len(CSV_CONTENT) - 1)
    def test_oversized_file_is_rejected(self):
        self.assertRejected(self.post_file("invoices.csv", CSV_CONTENT))

    def test_stored_file_is_removed_when_the_database_write_fails(self):
        def fail_after_storing(compiler, *args, **kwargs):
            compiler.as_sql()
            raise DatabaseError("connection lost")

        with (
            mock.patch.object(
                SQLInsertCompiler,
                "execute_sql",
                autospec=True,
                side_effect=fail_after_storing,
            ) as execute_sql,
            self.assertLogs("apps.imports.views", level="ERROR"),
        ):
            response = self.post_file("invoices.csv", CSV_CONTENT)

        execute_sql.assert_called_once()
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertNotIn("connection lost", str(response.data))
        self.assertEqual(self.stored_files(), [])

    def test_storage_failure_does_not_expose_paths(self):
        with (
            mock.patch(
                "django.core.files.storage.FileSystemStorage._save",
                side_effect=OSError("/app/media/secret"),
            ),
            self.assertLogs("apps.imports.views", level="ERROR"),
        ):
            response = self.post_file("invoices.csv", CSV_CONTENT)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertNotIn("/app/media", str(response.data))
        self.assertFalse(Upload.objects.exists())


class UploadResultTestCase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.merchant = User.objects.create_user("merchant", "m@example.com", "pass")
        cls.other_merchant = User.objects.create_user("other", "o@example.com", "pass")

    def setUp(self):
        self.client.force_authenticate(self.merchant)

    def make_upload(self, merchant=None, **fields):
        return Upload.objects.create(
            merchant=merchant or self.merchant,
            original_filename="invoices.csv",
            file="uploads/1/invoices.csv",
            **fields,
        )


class UploadStatusApiTests(UploadResultTestCase):
    def get_status(self, upload):
        return self.client.get(reverse("upload-status", args=[upload.id]))

    def test_every_status_is_returned(self):
        for value in Upload.Status.values:
            with self.subTest(status=value):
                upload = self.make_upload(status=value)

                response = self.get_status(upload)

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data["status"], value)

    def test_response_contains_the_upload_counts(self):
        upload = self.make_upload(
            status=Upload.Status.COMPLETED,
            total_rows=10,
            processed_rows=10,
            successful_rows=7,
            failed_rows=3,
        )

        response = self.get_status(upload)

        self.assertEqual(
            set(response.data),
            {
                "upload_id",
                "status",
                "progress",
                "total_rows",
                "processed_rows",
                "successful_rows",
                "failed_rows",
                "failure_reason",
                "created_at",
            },
        )
        self.assertEqual(response.data["upload_id"], upload.id)
        self.assertEqual(response.data["total_rows"], 10)
        self.assertEqual(response.data["processed_rows"], 10)
        self.assertEqual(response.data["successful_rows"], 7)
        self.assertEqual(response.data["failed_rows"], 3)
        self.assertEqual(response.data["failure_reason"], "")

    def test_progress_reflects_the_rows_processed(self):
        cases = [
            (Upload.Status.PENDING, 0, 0, 0),
            (Upload.Status.PROCESSING, 0, 100, 0),
            (Upload.Status.PROCESSING, 1, 3, 33),
            (Upload.Status.PROCESSING, 999, 1000, 99),
            (Upload.Status.FAILED, 0, 0, 0),
            (Upload.Status.FAILED, 40, 100, 40),
        ]
        for value, processed, total, expected in cases:
            with self.subTest(status=value, processed=processed, total=total):
                upload = self.make_upload(
                    status=value, processed_rows=processed, total_rows=total
                )

                self.assertEqual(self.get_status(upload).data["progress"], expected)

    def test_only_a_completed_upload_shows_100(self):
        cases = [
            (Upload.Status.PENDING, 99),
            (Upload.Status.PROCESSING, 99),
            (Upload.Status.FAILED, 99),
            (Upload.Status.COMPLETED, 100),
        ]
        for value, expected in cases:
            with self.subTest(status=value):
                upload = self.make_upload(
                    status=value, processed_rows=100, total_rows=100
                )

                self.assertEqual(self.get_status(upload).data["progress"], expected)

    def test_failure_reason_is_returned(self):
        upload = self.make_upload(
            status=Upload.Status.FAILED, failure_reason="The file has no invoice rows."
        )

        response = self.get_status(upload)

        self.assertEqual(response.data["failure_reason"], "The file has no invoice rows.")

    def test_unauthenticated_request_is_rejected(self):
        upload = self.make_upload()
        self.client.force_authenticate(None)

        self.assertEqual(
            self.get_status(upload).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_other_merchants_upload_looks_missing(self):
        upload = self.make_upload(merchant=self.other_merchant)

        response = self.get_status(upload)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_missing_upload_returns_not_found(self):
        response = self.client.get(reverse("upload-status", args=[999999]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_write_methods_are_not_allowed(self):
        upload = self.make_upload()
        url = reverse("upload-status", args=[upload.id])

        for method in (self.client.post, self.client.put, self.client.delete):
            with self.subTest(method=method.__name__):
                self.assertEqual(
                    method(url).status_code, status.HTTP_405_METHOD_NOT_ALLOWED
                )


class UploadErrorReportApiTests(UploadResultTestCase):
    header = ["row_number", "column", "message"]

    def get_report(self, upload, **headers):
        return self.client.get(reverse("upload-errors", args=[upload.id]), **headers)

    def read_csv(self, response):
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        return list(csv.reader(io.StringIO(content)))

    def add_errors(self, upload, *rows):
        UploadRowError.objects.bulk_create(
            UploadRowError(upload=upload, row_number=number, errors=errors)
            for number, errors in rows
        )

    def test_report_is_a_streamed_csv_download(self):
        upload = self.make_upload()

        response = self.get_report(upload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response, StreamingHttpResponse)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(
            response["Content-Disposition"],
            f'attachment; filename="upload-{upload.id}-errors.csv"',
        )

    def test_report_has_one_line_per_message_in_row_order(self):
        upload = self.make_upload(status=Upload.Status.COMPLETED)
        self.add_errors(
            upload,
            (9, {"status": ["Invalid status."]}),
            (3, {"amount": ["Not a number.", "Too long."], "customer_name": ["Required."]}),
        )

        rows = self.read_csv(self.get_report(upload))

        self.assertEqual(
            rows,
            [
                self.header,
                ["3", "amount", "Not a number."],
                ["3", "amount", "Too long."],
                ["3", "customer_name", "Required."],
                ["9", "status", "Invalid status."],
            ],
        )

    def test_messages_with_commas_quotes_and_unicode_stay_in_one_cell(self):
        upload = self.make_upload()
        message = 'Value "a,b" is “wrong”\nhere'
        self.add_errors(upload, (2, {"status": [message]}))

        rows = self.read_csv(self.get_report(upload))

        self.assertEqual(rows[1], ["2", "status", message])

    def test_cells_cannot_start_as_a_formula(self):
        upload = self.make_upload()
        triggers = ["=1+1", "+1+1", "-1+1", "@SUM(1)", "\t=1+1", "\r=1+1", "\n=1+1"]
        self.add_errors(
            upload,
            *((number, {"status": [value]}) for number, value in enumerate(triggers, 2)),
        )

        rows = self.read_csv(self.get_report(upload))

        self.assertEqual([row[2] for row in rows[1:]], [f"'{value}" for value in triggers])

    def test_harmless_messages_are_not_changed(self):
        upload = self.make_upload()
        self.add_errors(upload, (2, {"status": ["Enter a valid number."]}))

        rows = self.read_csv(self.get_report(upload))

        self.assertEqual(rows[1], ["2", "status", "Enter a valid number."])

    def test_report_starts_with_a_byte_order_mark_once(self):
        upload = self.make_upload()
        self.add_errors(upload, (2, {"status": ["Bad."]}))

        content = b"".join(self.get_report(upload).streaming_content)

        self.assertTrue(content.startswith(b"\xef\xbb\xbfrow_number,"))
        self.assertEqual(content.count(b"\xef\xbb\xbf"), 1)

    def test_upload_without_errors_returns_only_the_header(self):
        upload = self.make_upload(status=Upload.Status.COMPLETED)

        self.assertEqual(self.read_csv(self.get_report(upload)), [self.header])

    def test_failed_upload_without_row_errors_returns_only_the_header(self):
        upload = self.make_upload(status=Upload.Status.FAILED, failure_reason="Bad file.")

        self.assertEqual(self.read_csv(self.get_report(upload)), [self.header])

    def test_report_includes_only_that_uploads_errors(self):
        upload = self.make_upload()
        other_upload = self.make_upload()
        self.add_errors(upload, (2, {"status": ["Mine."]}))
        self.add_errors(other_upload, (2, {"status": ["Not mine."]}))

        rows = self.read_csv(self.get_report(upload))

        self.assertEqual(rows[1:], [["2", "status", "Mine."]])

    def test_large_report_keeps_every_row_in_order(self):
        upload = self.make_upload()
        self.add_errors(
            upload, *((number, {"status": ["Bad."]}) for number in range(2, 5002))
        )

        rows = self.read_csv(self.get_report(upload))

        self.assertEqual([int(row[0]) for row in rows[1:]], list(range(2, 5002)))

    def test_text_csv_accept_header_is_supported(self):
        upload = self.make_upload()

        response = self.get_report(upload, HTTP_ACCEPT="text/csv")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unauthenticated_request_is_rejected(self):
        upload = self.make_upload()
        self.client.force_authenticate(None)

        response = self.get_report(upload)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_other_merchants_report_looks_missing(self):
        upload = self.make_upload(merchant=self.other_merchant)
        self.add_errors(upload, (2, {"status": ["Private."]}))

        response = self.get_report(upload)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotIn(b"Private", response.content)

    def test_missing_upload_returns_not_found(self):
        response = self.client.get(reverse("upload-errors", args=[999999]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
