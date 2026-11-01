import io
import tempfile
import tracemalloc
import zipfile
from datetime import date, datetime
from decimal import Decimal
from inspect import isgenerator
from pathlib import Path
from threading import Thread
from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from kombu.exceptions import OperationalError as BrokerError
from openpyxl import Workbook, load_workbook
from rest_framework import status
from rest_framework.test import APITestCase

from apps.imports.models import Upload, UploadRowError
from apps.imports.parsing import iter_rows
from apps.imports.tasks import _publish, _save_batch, process_upload
from apps.invoices.models import Invoice
from apps.invoices.validation import validate_row

User = get_user_model()
real_save_batch = _save_batch

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
        self.assertEqual(upload.successful_rows, rows)
        self.assertEqual(upload.failed_rows, 0)
        self.assertEqual(upload.failure_reason, "")
        self.assertEqual(upload.invoices.count(), rows)

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
            return True

        with mock.patch("apps.imports.tasks._process_rows", side_effect=record):
            self.run_task(upload)

        self.assertEqual(seen, {"status": Upload.Status.PROCESSING, "total": 3})

    def test_rows_are_saved_in_batches(self):
        upload = self.make_upload(csv_content(rows=5))
        sizes = []

        def record(_, processed, rows):
            sizes.append((processed, len(rows)))
            return True

        with (
            mock.patch("apps.imports.tasks.BATCH_SIZE", 2),
            mock.patch("apps.imports.tasks._save_batch", side_effect=record),
        ):
            self.run_task(upload)

        self.assertEqual(sizes, [(0, 2), (2, 2), (4, 1)])

    def test_invalid_rows_are_recorded_alongside_valid_ones(self):
        content = HEADER + (
            "INV-1,Acme,10.00,2026-01-15,paid\n"
            "\n"
            ",,abc,2026-02-30,done\n"
            "INV-2,Globex,5.00,2026-01-16,OVERDUE\n"
        )
        upload = self.make_upload(content.encode())

        self.run_task(upload)

        self.assertEqual(upload.status, Upload.Status.COMPLETED)
        self.assertEqual(upload.total_rows, 3)
        self.assertEqual(upload.processed_rows, 3)
        self.assertEqual(upload.successful_rows, 2)
        self.assertEqual(upload.failed_rows, 1)
        self.assertEqual(
            sorted(upload.invoices.values_list("row_number", "invoice_number")),
            [(2, "INV-1"), (5, "INV-2")],
        )
        self.assertEqual(upload.invoices.get(row_number=5).status, "overdue")
        row_error = upload.row_errors.get()
        self.assertEqual(row_error.row_number, 4)
        self.assertEqual(
            set(row_error.errors),
            {"invoice_number", "customer_name", "amount", "invoice_date", "status"},
        )
        for messages in row_error.errors.values():
            self.assertIsInstance(messages, list)

    def test_upload_of_only_invalid_rows_completes(self):
        upload = self.make_upload((HEADER + "INV-1,Acme,abc,2026-01-15,paid\n").encode())

        self.run_task(upload)

        self.assertEqual(upload.status, Upload.Status.COMPLETED)
        self.assertEqual(upload.successful_rows, 0)
        self.assertEqual(upload.failed_rows, 1)
        self.assertEqual(upload.failure_reason, "")

    def test_xlsx_values_are_stored_with_exact_types(self):
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet()
        sheet.append(COLUMNS)
        sheet.append([1001, "Acme", 10.1, datetime(2026, 1, 15), "Paid"])
        buffer = io.BytesIO()
        workbook.save(buffer)
        upload = self.make_upload(buffer.getvalue(), "invoices.xlsx")

        self.run_task(upload)

        invoice = upload.invoices.get()
        self.assertEqual(invoice.invoice_number, "1001")
        self.assertEqual(invoice.amount, Decimal("10.10"))
        self.assertEqual(invoice.invoice_date, date(2026, 1, 15))
        self.assertEqual(invoice.status, "paid")

    def test_finished_upload_is_not_processed_again(self):
        upload = self.make_upload(csv_content())
        Upload.objects.filter(pk=upload.pk).update(
            status=Upload.Status.COMPLETED, processed_rows=2
        )

        with mock.patch("apps.imports.tasks._count_rows") as count_rows:
            self.run_task(upload)

        count_rows.assert_not_called()
        self.assertEqual(upload.processed_rows, 2)

    def test_unknown_upload_is_ignored(self):
        result = process_upload.apply(args=[0])

        self.assertTrue(result.successful())


class DuplicateInvoiceNumberTests(UploadTaskTestCase):
    message = {"invoice_number": ["This invoice number already exists."]}

    def rows(self, *numbers):
        lines = [f"{n},Acme,10.00,2026-01-15,paid\n" for n in numbers]
        return (HEADER + "".join(lines)).encode()

    def test_duplicate_within_a_batch_is_reported(self):
        upload = self.make_upload(self.rows("INV-1", "INV-2", "INV-1"))

        self.run_task(upload)

        self.assertEqual(upload.successful_rows, 2)
        row_error = upload.row_errors.get()
        self.assertEqual(row_error.row_number, 4)
        self.assertEqual(row_error.errors, self.message)
        self.assertEqual(upload.invoices.get(invoice_number="INV-1").row_number, 2)

    def test_duplicate_in_a_later_batch_is_reported(self):
        upload = self.make_upload(self.rows("INV-1", "INV-2", "INV-3", "INV-1"))

        with mock.patch("apps.imports.tasks.BATCH_SIZE", 2):
            self.run_task(upload)

        self.assertEqual(upload.successful_rows, 3)
        self.assertEqual(upload.row_errors.get().row_number, 5)

    def test_number_from_a_previous_upload_is_reported(self):
        self.run_task(self.make_upload(self.rows("INV-1")))
        upload = self.make_upload(self.rows("INV-1", "INV-2"))

        self.run_task(upload)

        self.assertEqual(upload.successful_rows, 1)
        self.assertEqual(upload.failed_rows, 1)
        self.assertEqual(upload.row_errors.get().errors, self.message)

    def test_other_merchants_can_use_the_same_number(self):
        self.run_task(self.make_upload(self.rows("INV-1")))
        other = User.objects.create_user("other", "o@example.com", "pass")
        upload = Upload.objects.create(
            merchant=other,
            original_filename="invoices.csv",
            file=ContentFile(self.rows("INV-1"), name="invoices.csv"),
        )

        self.run_task(upload)

        self.assertEqual(upload.successful_rows, 1)

    def test_an_invalid_row_does_not_reserve_its_number(self):
        content = HEADER + (
            "INV-1,Acme,abc,2026-01-15,paid\n"
            "INV-1,Acme,10.00,2026-01-15,paid\n"
        )
        upload = self.make_upload(content.encode())

        self.run_task(upload)

        self.assertEqual(upload.successful_rows, 1)
        self.assertEqual(list(upload.row_errors.get().errors), ["amount"])

    def test_duplicate_is_reported_with_other_errors_in_the_row(self):
        content = HEADER + (
            "INV-1,Acme,10.00,2026-01-15,paid\n"
            "INV-1,Acme,abc,2026-01-15,paid\n"
        )
        upload = self.make_upload(content.encode())

        self.run_task(upload)

        self.assertEqual(
            set(upload.row_errors.get().errors), {"invoice_number", "amount"}
        )


class IdempotencyTests(UploadTaskTestCase):
    def fail_on_second_batch(self):
        calls = []

        def save(*args):
            calls.append(args)
            if len(calls) == 2:
                raise OperationalError("connection lost")
            return real_save_batch(*args)

        return mock.patch("apps.imports.tasks._save_batch", side_effect=save)

    def assertExactlyOnce(self, upload):
        self.assertEqual(upload.status, Upload.Status.COMPLETED)
        self.assertEqual(upload.processed_rows, 7)
        self.assertEqual(upload.successful_rows, 6)
        self.assertEqual(upload.failed_rows, 1)
        self.assertEqual(upload.invoices.count(), 6)
        self.assertEqual(upload.row_errors.count(), 1)

    def mixed_rows(self):
        return csv_content(rows=6) + b"INV-X,Acme,abc,2026-01-15,paid\n"

    def test_retry_resumes_after_the_last_committed_batch(self):
        upload = self.make_upload(self.mixed_rows())

        with mock.patch("apps.imports.tasks.BATCH_SIZE", 2), self.fail_on_second_batch():
            self.run_task(upload)

        self.assertExactlyOnce(upload)

    def test_redelivery_after_a_crash_resumes(self):
        upload = self.make_upload(self.mixed_rows())
        with (
            mock.patch("apps.imports.tasks.BATCH_SIZE", 2),
            self.fail_on_second_batch(),
            mock.patch.object(process_upload, "retry", side_effect=RuntimeError),
        ):
            process_upload.apply(args=[upload.id])
        upload.refresh_from_db()
        self.assertEqual(upload.status, Upload.Status.PROCESSING)
        self.assertEqual(upload.processed_rows, 2)

        with mock.patch("apps.imports.tasks.BATCH_SIZE", 2):
            self.run_task(upload)

        self.assertExactlyOnce(upload)

    def test_batch_failure_rolls_back_invoices_errors_and_counters(self):
        upload = self.make_upload(self.mixed_rows())
        Upload.objects.filter(pk=upload.pk).update(status=Upload.Status.PROCESSING)
        cleaned, _ = validate_row(next(iter_rows(io.BytesIO(csv_content()), ".csv"))[1])
        rows = [(2, cleaned, {}), (3, {}, {"amount": ["Bad."]})]

        with (
            mock.patch.object(UploadRowError.objects, "bulk_create", side_effect=OperationalError),
            self.assertRaises(OperationalError),
        ):
            real_save_batch(upload, 0, rows)

        upload.refresh_from_db()
        self.assertEqual(upload.processed_rows, 0)
        self.assertEqual(upload.invoices.count(), 0)
        self.assertEqual(upload.row_errors.count(), 0)

    def test_duplicate_delivery_stops_without_writing(self):
        upload = self.make_upload(csv_content())
        Upload.objects.filter(pk=upload.pk).update(
            status=Upload.Status.PROCESSING, processed_rows=5
        )

        saved = real_save_batch(upload, 0, [(2, {}, {"amount": ["Bad."]})])

        self.assertFalse(saved)
        self.assertEqual(upload.row_errors.count(), 0)

    def test_stale_delivery_does_not_complete_the_upload(self):
        upload = self.make_upload(csv_content())

        with mock.patch("apps.imports.tasks._save_batch", return_value=False):
            self.run_task(upload)

        self.assertEqual(upload.status, Upload.Status.PROCESSING)


class ConcurrencyTests(TransactionTestCase):
    def test_concurrent_uploads_cannot_store_the_same_number_twice(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        merchant = User.objects.create_user("merchant", "m@example.com", "pass")
        errors = []

        def run(upload_id):
            try:
                process_upload.apply(args=[upload_id], throw=True)
            except Exception as error:
                errors.append(error)
            finally:
                connections.close_all()

        with (
            override_settings(MEDIA_ROOT=media.name),
            mock.patch("apps.imports.tasks.BATCH_SIZE", 10),
        ):
            uploads = [
                Upload.objects.create(
                    merchant=merchant,
                    original_filename="invoices.csv",
                    file=ContentFile(csv_content(rows=100), name="invoices.csv"),
                )
                for _ in range(2)
            ]
            threads = [Thread(target=run, args=[upload.id]) for upload in uploads]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(errors, [])
        for upload in uploads:
            upload.refresh_from_db()
            self.assertEqual(upload.status, Upload.Status.COMPLETED)
        self.assertEqual(Invoice.objects.count(), 100)
        self.assertEqual(UploadRowError.objects.count(), 100)
        self.assertEqual(sum(upload.successful_rows for upload in uploads), 100)
        self.assertEqual(sum(upload.failed_rows for upload in uploads), 100)


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

                with mock.patch("apps.imports.tasks._process_rows") as process_rows:
                    self.run_task(upload)

                process_rows.assert_not_called()
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
            "apps.imports.tasks._process_rows",
            side_effect=[OperationalError("connection lost"), True],
        ) as process_rows:
            self.run_task(upload)

        self.assertEqual(process_rows.call_count, 2)
        self.assertEqual(upload.status, Upload.Status.COMPLETED)

    def test_upload_fails_when_retries_run_out(self):
        upload = self.make_upload(csv_content())

        with (
            mock.patch(
                "apps.imports.tasks._process_rows",
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
                "apps.imports.tasks._process_rows",
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
                "apps.imports.tasks._process_rows", side_effect=SoftTimeLimitExceeded
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

    def test_task_is_queued_in_a_thread_only_after_commit(self):
        with mock.patch("apps.imports.tasks.threading.Thread") as thread:
            with self.captureOnCommitCallbacks() as callbacks:
                response = self.post_file()

            thread.assert_not_called()
            self.assertEqual(len(callbacks), 1)

            callbacks[0]()

        upload_id = response.data["upload_id"]
        self.assertEqual(thread.call_args.kwargs["args"], (upload_id,))
        thread.return_value.start.assert_called_once_with()

    def test_publish_sends_the_task(self):
        with mock.patch.object(process_upload, "delay") as delay:
            _publish(5)

        delay.assert_called_once_with(5)

    def test_broker_failure_fails_the_upload(self):
        self.post_file()
        upload = Upload.objects.get()

        with (
            mock.patch.object(process_upload, "delay", side_effect=BrokerError("down")),
            self.assertLogs("apps.imports.tasks", level="ERROR"),
        ):
            _publish(upload.id)

        upload.refresh_from_db()
        self.assertEqual(upload.status, Upload.Status.FAILED)
        self.assertEqual(
            upload.failure_reason, "The upload could not be queued for processing."
        )
        self.assertTrue(Path(upload.file.path).exists())
