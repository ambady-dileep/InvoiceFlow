import io
import tempfile
import tracemalloc
import zipfile
from inspect import isgenerator
from pathlib import Path
from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError
from django.test import TestCase, override_settings
from django.urls import reverse
from kombu.exceptions import OperationalError as BrokerError
from openpyxl import Workbook, load_workbook
from rest_framework import status
from rest_framework.test import APITestCase

from apps.imports.models import Upload
from apps.imports.parsing import iter_rows
from apps.imports.tasks import process_upload

User = get_user_model()

HEADER = "invoice_number,customer_name,amount,invoice_date,status\n"
COLUMNS = HEADER.strip().split(",")


def csv_content(rows=2):
    lines = [f"INV-{i},Acme,10.00,2026-01-15,pending\n" for i in range(rows)]
    return (HEADER + "".join(lines)).encode()


def xlsx_content(rows=2, columns=COLUMNS):
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet()
    sheet.append(columns)
    for i in range(rows):
        sheet.append([f"INV-{i}", "Acme", 10.5, "2026-01-15", "pending"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def zip_content(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class UploadTaskTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.merchant = User.objects.create_user("merchant", "m@example.com", "pass")

    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        settings_override = override_settings(MEDIA_ROOT=media.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

    def make_upload(self, content, name="invoices.csv"):
        return Upload.objects.create(
            merchant=self.merchant,
            original_filename=name,
            file=ContentFile(content, name=name),
        )

    def run_task(self, upload, **options):
        process_upload.apply(args=[upload.id], **options)
        upload.refresh_from_db()

    def assertFailed(self, upload, reason):
        self.assertEqual(upload.status, Upload.Status.FAILED)
        self.assertEqual(upload.failure_reason, reason)
        self.assertEqual(upload.processed_rows, 0)


class ProcessingTests(UploadTaskTestCase):
    def assertCompleted(self, upload, rows):
        self.assertEqual(upload.status, Upload.Status.COMPLETED)
        self.assertEqual(upload.total_rows, rows)
        self.assertEqual(upload.processed_rows, rows)
        self.assertEqual(upload.failure_reason, "")

    def test_csv_is_processed(self):
        upload = self.make_upload(csv_content(rows=3))

        self.run_task(upload)

        self.assertCompleted(upload, 3)

    def test_xlsx_is_processed(self):
        upload = self.make_upload(xlsx_content(rows=3), "invoices.xlsx")

        self.run_task(upload)

        self.assertCompleted(upload, 3)

    def test_blank_rows_are_not_counted(self):
        content = HEADER + "INV-1,Acme,10,2026-01-15,paid\n\n,,,,\n  ,,,,\n"
        upload = self.make_upload(content.encode())

        self.run_task(upload)

        self.assertCompleted(upload, 1)

    def test_status_is_processing_with_a_total_while_rows_are_read(self):
        upload = self.make_upload(csv_content(rows=3))
        seen = {}

        def record(claimed):
            current = Upload.objects.get(pk=claimed.pk)
            seen.update(status=current.status, total=current.total_rows)
            return 3

        with mock.patch("apps.imports.tasks._read_rows", side_effect=record):
            self.run_task(upload)

        self.assertEqual(seen, {"status": Upload.Status.PROCESSING, "total": 3})

    def test_progress_is_saved_in_batches(self):
        upload = self.make_upload(csv_content(rows=5))
        saved = []

        with (
            mock.patch("apps.imports.tasks.PROGRESS_INTERVAL", 2),
            mock.patch(
                "apps.imports.tasks._update",
                side_effect=lambda _, **fields: saved.append(fields),
            ),
        ):
            self.run_task(upload)

        progress = [fields["processed_rows"] for fields in saved if "processed_rows" in fields]
        self.assertEqual(progress, [2, 4, 5])

    def test_finished_upload_is_not_processed_again(self):
        upload = self.make_upload(csv_content())
        Upload.objects.filter(pk=upload.pk).update(
            status=Upload.Status.COMPLETED, processed_rows=2
        )

        with mock.patch("apps.imports.tasks._count_rows") as count_rows:
            self.run_task(upload)

        count_rows.assert_not_called()
        self.assertEqual(upload.processed_rows, 2)

    def test_upload_left_processing_by_a_crash_is_restarted(self):
        upload = self.make_upload(csv_content(rows=3))
        Upload.objects.filter(pk=upload.pk).update(
            status=Upload.Status.PROCESSING, processed_rows=1000
        )

        self.run_task(upload)

        self.assertCompleted(upload, 3)

    def test_unknown_upload_is_ignored(self):
        result = process_upload.apply(args=[0])

        self.assertTrue(result.successful())


class RowLimitTests(UploadTaskTestCase):
    @override_settings(MAX_UPLOAD_ROWS=3)
    def test_file_at_the_limit_is_processed(self):
        for content, name in (
            (csv_content(rows=3), "invoices.csv"),
            (xlsx_content(rows=3), "invoices.xlsx"),
        ):
            with self.subTest(name=name):
                upload = self.make_upload(content, name)

                self.run_task(upload)

                self.assertEqual(upload.status, Upload.Status.COMPLETED)

    @override_settings(MAX_UPLOAD_ROWS=3)
    def test_file_over_the_limit_fails_before_rows_are_read(self):
        for content, name in (
            (csv_content(rows=4), "invoices.csv"),
            (xlsx_content(rows=4), "invoices.xlsx"),
        ):
            with self.subTest(name=name):
                upload = self.make_upload(content, name)

                with mock.patch("apps.imports.tasks._read_rows") as read_rows:
                    self.run_task(upload)

                read_rows.assert_not_called()
                self.assertFailed(upload, "The file has more than 3 rows.")


class MalformedFileTests(UploadTaskTestCase):
    invalid_xlsx = "The file is not a valid XLSX file."

    def test_malformed_csv_files_fail(self):
        cases = [
            (b"invoice_number,amount\nINV-1,5\n", "Missing required columns: customer_name, invoice_date, status."),
            (HEADER.encode(), "The file has no invoice rows."),
            (b"\n\n", "The file has no header row."),
            (csv_content() + b"\xff\xfe\n", "CSV files must be UTF-8 encoded."),
            (
                HEADER.encode() + b"x" * 200_000 + b",a,b,c,d\n",
                "The file is not a valid CSV file.",
            ),
        ]
        for content, reason in cases:
            with self.subTest(reason=reason):
                upload = self.make_upload(content)

                self.run_task(upload)

                self.assertFailed(upload, reason)

    def test_malformed_xlsx_files_fail(self):
        cases = [
            (b"not a zip archive", self.invalid_xlsx),
            (zip_content({"readme.txt": "text"}), self.invalid_xlsx),
            (
                zip_content({"[Content_Types].xml": "<broken", "xl/workbook.xml": "<broken"}),
                self.invalid_xlsx,
            ),
            (xlsx_content(rows=2)[:-200], self.invalid_xlsx),
            (
                xlsx_content(columns=["invoice_number", "amount"]),
                "Missing required columns: customer_name, invoice_date, status.",
            ),
            (xlsx_content(rows=0), "The file has no invoice rows."),
        ]
        for content, reason in cases:
            with self.subTest(content=content[:30]):
                upload = self.make_upload(content, "invoices.xlsx")

                self.run_task(upload)

                self.assertFailed(upload, reason)

    def test_corrupt_worksheet_data_fails(self):
        source = zipfile.ZipFile(io.BytesIO(xlsx_content()))
        entries = {name: source.read(name) for name in source.namelist()}
        entries["xl/worksheets/sheet1.xml"] = b"<worksheet><sheetData><row"
        upload = self.make_upload(zip_content(entries), "invoices.xlsx")

        self.run_task(upload)

        self.assertFailed(upload, self.invalid_xlsx)

    @override_settings(MAX_XLSX_UNCOMPRESSED_SIZE=1024)
    def test_excessive_decompressed_size_fails_before_parsing(self):
        content = zip_content({"xl/sharedStrings.xml": "a" * 2048})
        upload = self.make_upload(content, "invoices.xlsx")

        with mock.patch("apps.imports.parsing.load_workbook") as load_workbook:
            self.run_task(upload)

        load_workbook.assert_not_called()
        self.assertFailed(upload, "The XLSX file is too large when decompressed.")


class FailureHandlingTests(UploadTaskTestCase):
    def test_missing_file_fails_without_retry(self):
        upload = self.make_upload(csv_content())
        upload.file.storage.delete(upload.file.name)

        with (
            mock.patch.object(process_upload, "retry") as retry,
            self.assertLogs("apps.imports.tasks", level="ERROR"),
        ):
            self.run_task(upload)

        retry.assert_not_called()
        self.assertFailed(upload, "The uploaded file could not be found.")

    def test_invalid_file_is_not_retried(self):
        upload = self.make_upload(b"invoice_number\nINV-1\n")

        with mock.patch.object(process_upload, "retry") as retry:
            self.run_task(upload)

        retry.assert_not_called()
        self.assertEqual(upload.status, Upload.Status.FAILED)

    def test_transient_errors_are_retried_with_backoff(self):
        for error in (OperationalError("connection lost"), OSError("disk busy")):
            with self.subTest(error=error):
                upload = self.make_upload(csv_content())

                with (
                    mock.patch("apps.imports.tasks._count_rows", side_effect=error),
                    mock.patch.object(
                        process_upload, "retry", side_effect=RuntimeError
                    ) as retry,
                ):
                    process_upload.apply(args=[upload.id], retries=1)

                retry.assert_called_once_with(exc=error, countdown=60)

    def test_retry_completes_after_a_transient_error(self):
        upload = self.make_upload(csv_content(rows=2))

        with mock.patch(
            "apps.imports.tasks._read_rows",
            side_effect=[OperationalError("connection lost"), 2],
        ) as read_rows:
            self.run_task(upload)

        self.assertEqual(read_rows.call_count, 2)
        self.assertEqual(upload.status, Upload.Status.COMPLETED)

    def test_upload_fails_when_retries_run_out(self):
        upload = self.make_upload(csv_content())

        with (
            mock.patch(
                "apps.imports.tasks._read_rows",
                side_effect=OperationalError("password=secret"),
            ),
            self.assertLogs("apps.imports.tasks", level="ERROR"),
        ):
            self.run_task(upload, retries=process_upload.max_retries)

        self.assertEqual(upload.status, Upload.Status.FAILED)
        self.assertEqual(
            upload.failure_reason,
            "The upload could not be processed. Please try again.",
        )

    def test_unexpected_error_fails_without_exposing_details(self):
        upload = self.make_upload(csv_content())

        with (
            mock.patch(
                "apps.imports.tasks._read_rows",
                side_effect=ZeroDivisionError("/app/x"),
            ),
            self.assertLogs("apps.imports.tasks", level="ERROR"),
        ):
            self.run_task(upload)

        self.assertFailed(upload, "The upload could not be processed.")

    def test_time_limit_fails_the_upload(self):
        upload = self.make_upload(csv_content())

        with (
            mock.patch(
                "apps.imports.tasks._read_rows", side_effect=SoftTimeLimitExceeded
            ),
            self.assertLogs("apps.imports.tasks", level="ERROR"),
        ):
            self.run_task(upload)

        self.assertFailed(upload, "Processing took too long.")


class MemoryUsageTests(TestCase):
    def test_csv_peak_memory_does_not_grow_with_the_file(self):
        content = csv_content(rows=20_000)

        tracemalloc.start()
        self.addCleanup(tracemalloc.stop)
        count = sum(1 for _ in iter_rows(io.BytesIO(content), ".csv"))
        _, peak = tracemalloc.get_traced_memory()

        self.assertEqual(count, 20_000)
        self.assertLess(peak, len(content) / 4)

    def test_rows_are_read_lazily(self):
        rows = iter_rows(io.BytesIO(csv_content()), ".csv")

        self.assertTrue(isgenerator(rows))

    def test_xlsx_uses_read_only_mode(self):
        with mock.patch(
            "apps.imports.parsing.load_workbook", wraps=load_workbook
        ) as wrapped:
            list(iter_rows(io.BytesIO(xlsx_content()), ".xlsx"))

        self.assertTrue(wrapped.call_args.kwargs["read_only"])


class DispatchTests(APITestCase):
    url = reverse("upload")

    @classmethod
    def setUpTestData(cls):
        cls.merchant = User.objects.create_user("merchant", "m@example.com", "pass")

    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        settings_override = override_settings(MEDIA_ROOT=media.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.client.force_authenticate(self.merchant)

    def post_file(self):
        file = SimpleUploadedFile("invoices.csv", csv_content(), content_type="text/csv")
        return self.client.post(self.url, {"file": file}, format="multipart")

    def test_task_is_queued_only_after_commit(self):
        with mock.patch.object(process_upload, "delay") as delay:
            with self.captureOnCommitCallbacks() as callbacks:
                response = self.post_file()

            delay.assert_not_called()
            self.assertEqual(len(callbacks), 1)

            callbacks[0]()

        delay.assert_called_once_with(response.data["upload_id"])

    def test_broker_failure_fails_the_upload(self):
        with (
            mock.patch.object(process_upload, "delay", side_effect=BrokerError("down")),
            self.assertLogs("apps.imports.tasks", level="ERROR"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.post_file()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        upload = Upload.objects.get()
        self.assertEqual(upload.status, Upload.Status.FAILED)
        self.assertEqual(
            upload.failure_reason, "The upload could not be queued for processing."
        )
        self.assertTrue(Path(upload.file.path).exists())
