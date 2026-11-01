import csv
import io
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import DatabaseError, OperationalError
from django.test import override_settings
from django.urls import reverse
from kombu.exceptions import OperationalError as BrokerError
from rest_framework import status
from rest_framework.test import APITestCase

from apps.imports.models import Upload
from apps.invoices.models import Invoice, InvoiceExport
from apps.invoices.tasks import _publish, generate_export

User = get_user_model()

COLUMNS = ["invoice_number", "customer_name", "amount", "invoice_date", "status"]


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
)
class ExportTestCase(APITestCase):
    url = reverse("invoice-export")

    @classmethod
    def setUpTestData(cls):
        cls.merchant = User.objects.create_user("merchant", "m@example.com", "pass")
        cls.other_merchant = User.objects.create_user("other", "o@example.com", "pass")
        cls.upload = cls.make_upload(cls.merchant)
        cls.other_upload = cls.make_upload(cls.other_merchant)
        cls.create("INV-001", "Acme Traders", "100.00", date(2026, 1, 10), "paid")
        cls.create("INV-002", "Globex", "250.50", date(2026, 1, 20), "pending")
        cls.create("INV-003", "acme retail", "999.99", date(2026, 2, 5), "overdue")
        cls.create(
            "OTHER-1",
            "Acme Hidden",
            "500.00",
            date(2026, 1, 15),
            "paid",
            merchant=cls.other_merchant,
            upload=cls.other_upload,
        )

    @classmethod
    def make_upload(cls, merchant):
        return Upload.objects.create(
            merchant=merchant,
            original_filename="invoices.csv",
            file="uploads/1/invoices.csv",
        )

    @classmethod
    def create(
        cls, number, customer, amount, invoice_date, state, merchant=None, upload=None
    ):
        upload = upload or cls.upload
        return Invoice.objects.create(
            merchant=merchant or cls.merchant,
            upload=upload,
            row_number=upload.invoices.count() + 2,
            invoice_number=number,
            customer_name=customer,
            amount=Decimal(amount),
            invoice_date=invoice_date,
            status=state,
        )

    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media_root = Path(media.name)
        settings_override = override_settings(MEDIA_ROOT=media.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.client.force_authenticate(self.merchant)

    def make_export(self, merchant=None, **fields):
        return InvoiceExport.objects.create(merchant=merchant or self.merchant, **fields)

    def run_export(self, query="", merchant=None):
        export = self.make_export(merchant, query=query)
        generate_export.apply(args=[export.id])
        export.refresh_from_db()
        return export

    def read_rows(self, export):
        with export.file.open("rb") as file:
            content = file.read().decode("utf-8-sig")
        return list(csv.reader(io.StringIO(content, newline="")))

    def numbers(self, export):
        return [row[0] for row in self.read_rows(export)[1:]]

    def stored_files(self):
        return [path for path in self.media_root.rglob("*") if path.is_file()]


class ExportCreateTests(ExportTestCase):
    def post(self, query=""):
        with mock.patch("apps.invoices.export_views.queue_export"):
            return self.client.post(f"{self.url}?{query}")

    def test_export_is_accepted_for_the_request_user(self):
        response = self.post()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        export = InvoiceExport.objects.get()
        self.assertEqual(export.merchant, self.merchant)
        self.assertEqual(export.status, InvoiceExport.Status.PENDING)
        self.assertEqual(response.data["export_id"], export.id)
        self.assertIsNone(response.data["download_url"])

    def test_merchant_parameter_cannot_change_the_owner(self):
        self.post(f"merchant={self.other_merchant.id}")

        self.assertEqual(InvoiceExport.objects.get().merchant, self.merchant)

    def test_filters_are_kept_and_pagination_is_dropped(self):
        self.post("status=paid&search=acme&ordering=amount&page=3&page_size=10")

        query = InvoiceExport.objects.get().query
        self.assertIn("status=paid", query)
        self.assertIn("search=acme", query)
        self.assertIn("ordering=amount", query)
        self.assertNotIn("page", query)

    def test_invalid_filters_are_rejected_before_queuing(self):
        for query, field in (
            ("status=cancelled", "status"),
            ("amount__gte=abc", "amount__gte"),
            ("invoice_date__lte=2026-02-30", "invoice_date__lte"),
        ):
            with self.subTest(query=query):
                response = self.post(query)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)
                self.assertFalse(InvoiceExport.objects.exists())

    def test_unauthenticated_request_is_rejected(self):
        self.client.force_authenticate(None)

        response = self.post()

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(InvoiceExport.objects.exists())

    def test_task_is_queued_in_a_thread_only_after_commit(self):
        with mock.patch("apps.invoices.tasks.threading.Thread") as thread:
            with self.captureOnCommitCallbacks() as callbacks:
                response = self.client.post(self.url)

            thread.assert_not_called()
            self.assertEqual(len(callbacks), 1)

            callbacks[0]()

        self.assertEqual(
            thread.call_args.kwargs["args"], (response.data["export_id"],)
        )
        thread.return_value.start.assert_called_once_with()

    def test_broker_failure_fails_the_export(self):
        export = self.make_export()

        with (
            mock.patch.object(generate_export, "delay", side_effect=BrokerError("down")),
            self.assertLogs("apps.invoices.tasks", level="ERROR"),
        ):
            _publish(export.id)

        export.refresh_from_db()
        self.assertEqual(export.status, InvoiceExport.Status.FAILED)
        self.assertEqual(export.failure_reason, "The export could not be queued.")


class ExportStatusTests(ExportTestCase):
    def get_status(self, export):
        return self.client.get(reverse("invoice-export-detail", args=[export.id]))

    def test_pending_export_has_no_download_url(self):
        for state in ("pending", "processing", "failed"):
            with self.subTest(state=state):
                response = self.get_status(self.make_export(status=state))

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data["status"], state)
                self.assertIsNone(response.data["download_url"])

    def test_completed_export_has_the_authenticated_download_path(self):
        export = self.run_export()

        response = self.get_status(export)

        self.assertEqual(
            set(response.data),
            {
                "export_id",
                "status",
                "row_count",
                "failure_reason",
                "download_url",
                "created_at",
            },
        )
        self.assertEqual(response.data["row_count"], 3)
        self.assertEqual(
            response.data["download_url"],
            reverse("invoice-export-download", args=[export.id]),
        )

    def test_stored_file_path_is_not_exposed(self):
        export = self.run_export()

        self.assertNotIn(export.file.name, str(self.get_status(export).data))

    def test_failure_reason_is_returned(self):
        export = self.make_export(status="failed", failure_reason="It failed.")

        self.assertEqual(self.get_status(export).data["failure_reason"], "It failed.")

    def test_unauthenticated_request_is_rejected(self):
        export = self.make_export()
        self.client.force_authenticate(None)

        self.assertEqual(
            self.get_status(export).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_other_merchants_export_looks_missing(self):
        export = self.make_export(self.other_merchant)

        self.assertEqual(self.get_status(export).status_code, status.HTTP_404_NOT_FOUND)

    def test_missing_export_returns_not_found(self):
        response = self.client.get(reverse("invoice-export-detail", args=[999999]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ExportDownloadTests(ExportTestCase):
    def download(self, export, **headers):
        url = reverse("invoice-export-download", args=[export.id])
        return self.client.get(url, **headers)

    def test_completed_export_downloads_as_csv(self):
        export = self.run_export()

        response = self.download(export)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(
            response["Content-Disposition"],
            f'attachment; filename="invoices-{export.id}.csv"',
        )
        content = b"".join(response.streaming_content)
        self.assertTrue(content.startswith(b"\xef\xbb\xbfinvoice_number,"))

    def test_text_csv_accept_header_is_supported(self):
        response = self.download(self.run_export(), HTTP_ACCEPT="text/csv")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unfinished_exports_cannot_be_downloaded(self):
        for state in ("pending", "processing", "failed"):
            with self.subTest(state=state):
                response = self.download(self.make_export(status=state))

                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_request_is_rejected(self):
        export = self.run_export()
        self.client.force_authenticate(None)

        self.assertEqual(self.download(export).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_other_merchants_download_looks_missing(self):
        export = self.run_export(merchant=self.other_merchant)

        response = self.download(export)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotIn(b"OTHER-1", response.content)

    def test_missing_stored_file_returns_not_found(self):
        export = self.run_export()
        export.file.storage.delete(export.file.name)

        self.assertEqual(self.download(export).status_code, status.HTTP_404_NOT_FOUND)

    def test_files_are_not_publicly_served(self):
        export = self.run_export()

        response = self.client.get(f"/media/{export.file.name}")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ExportContentTests(ExportTestCase):
    def test_csv_has_the_header_and_every_value(self):
        export = self.run_export("search=INV-001")

        self.assertEqual(export.status, InvoiceExport.Status.COMPLETED)
        self.assertEqual(export.row_count, 1)
        self.assertEqual(
            self.read_rows(export),
            [
                COLUMNS,
                ["INV-001", "Acme Traders", "100.00", "2026-01-10", "paid"],
            ],
        )

    def test_only_the_merchants_invoices_are_exported(self):
        self.assertCountEqual(
            self.numbers(self.run_export()), ["INV-001", "INV-002", "INV-003"]
        )

    def test_search_and_filters_cannot_reach_other_merchants(self):
        self.assertEqual(self.numbers(self.run_export("search=OTHER-1")), [])

    def test_merchant_in_the_query_cannot_switch_merchants(self):
        export = self.run_export(f"merchant={self.other_merchant.id}")

        self.assertNotIn("OTHER-1", self.numbers(export))

    def test_export_matches_the_list_for_each_filter(self):
        for query in (
            "status=paid",
            "search=acme",
            "invoice_date__gte=2026-01-15&invoice_date__lte=2026-01-31",
            "amount__gte=200&amount__lte=300",
            "search=acme&status=overdue",
            "ordering=-amount",
            "ordering=customer_name",
            "",
        ):
            with self.subTest(query=query):
                listed = self.client.get(f"{reverse('invoice-list')}?{query}")

                self.assertEqual(
                    self.numbers(self.run_export(query)),
                    [row["invoice_number"] for row in listed.data["results"]],
                )

    def test_every_matching_row_is_exported_past_the_list_page_size(self):
        Invoice.objects.bulk_create(
            Invoice(
                merchant=self.merchant,
                upload=self.upload,
                row_number=100 + number,
                invoice_number=f"BULK-{number}",
                customer_name="Bulk",
                amount=Decimal("1.00"),
                invoice_date=date(2026, 3, 1),
                status="paid",
            )
            for number in range(150)
        )

        export = self.run_export("search=BULK")

        self.assertEqual(export.row_count, 150)
        self.assertEqual(len(self.numbers(export)), 150)

    def test_no_matches_produce_a_header_only_file(self):
        export = self.run_export("search=nothing-matches")

        self.assertEqual(export.status, InvoiceExport.Status.COMPLETED)
        self.assertEqual(export.row_count, 0)
        self.assertEqual(self.read_rows(export), [COLUMNS])

    def test_text_cells_cannot_start_a_spreadsheet_formula(self):
        for number, value in enumerate(["=1+1", "+1", "-1", "@SUM(1)"]):
            self.create(f"F-{number}", value, "1.00", date(2026, 3, 1), "paid")
        self.create("=2+2", "Safe", "1.00", date(2026, 3, 2), "paid")

        rows = self.read_rows(self.run_export("ordering=invoice_number"))

        cells = [cell for row in rows[1:] for cell in row[:2]]
        self.assertFalse([cell for cell in cells if cell[0] in "=+-@"])
        self.assertIn("'=1+1", cells)
        self.assertIn("'=2+2", cells)

    def test_commas_quotes_and_unicode_are_preserved(self):
        self.create("Q-1", 'Müller, "Sons"', "1.00", date(2026, 3, 1), "paid")

        rows = self.read_rows(self.run_export("search=Q-1"))

        self.assertEqual(rows[1][1], 'Müller, "Sons"')

    def test_stored_file_uses_a_generated_private_name(self):
        export = self.run_export()

        self.assertRegex(
            export.file.name, rf"^exports/{self.merchant.id}/[0-9a-f]{{32}}\.csv$"
        )


class ExportFailureTests(ExportTestCase):
    def test_transient_errors_are_retried_with_backoff(self):
        export = self.make_export()
        error = OperationalError("connection lost")

        with (
            mock.patch("apps.invoices.tasks._write_export", side_effect=error),
            mock.patch.object(generate_export, "retry", side_effect=RuntimeError) as retry,
        ):
            generate_export.apply(args=[export.id], retries=1)

        retry.assert_called_once_with(exc=error, countdown=60)

    def test_retry_completes_after_a_transient_error(self):
        export = self.make_export()

        with mock.patch(
            "apps.invoices.tasks.filtered_invoices",
            side_effect=[
                OperationalError("connection lost"),
                Invoice.objects.filter(merchant=self.merchant),
            ],
        ):
            generate_export.apply(args=[export.id])

        export.refresh_from_db()
        self.assertEqual(export.status, InvoiceExport.Status.COMPLETED)
        self.assertEqual(export.row_count, 3)
        self.assertEqual(len(self.stored_files()), 1)

    def test_export_fails_when_retries_run_out(self):
        export = self.make_export()

        with (
            mock.patch(
                "apps.invoices.tasks._write_export",
                side_effect=OperationalError("password=secret"),
            ),
            self.assertLogs("apps.invoices.tasks", level="ERROR"),
        ):
            generate_export.apply(args=[export.id], retries=generate_export.max_retries)

        export.refresh_from_db()
        self.assertEqual(export.status, InvoiceExport.Status.FAILED)
        self.assertEqual(
            export.failure_reason, "The export could not be created. Please try again."
        )

    def test_unexpected_error_fails_without_exposing_details(self):
        export = self.make_export()

        with (
            mock.patch(
                "apps.invoices.tasks._write_export",
                side_effect=ZeroDivisionError("/app/secret"),
            ),
            self.assertLogs("apps.invoices.tasks", level="ERROR"),
        ):
            generate_export.apply(args=[export.id])

        export.refresh_from_db()
        self.assertEqual(export.status, InvoiceExport.Status.FAILED)
        self.assertEqual(export.failure_reason, "The export could not be created.")

    def test_time_limit_fails_the_export(self):
        export = self.make_export()

        with (
            mock.patch(
                "apps.invoices.tasks._write_export", side_effect=SoftTimeLimitExceeded
            ),
            self.assertLogs("apps.invoices.tasks", level="ERROR"),
        ):
            generate_export.apply(args=[export.id])

        export.refresh_from_db()
        self.assertEqual(export.failure_reason, "The export took too long.")

    def test_failure_while_writing_leaves_no_downloadable_file(self):
        export = self.make_export()

        with (
            mock.patch(
                "apps.invoices.tasks.safe_cell", side_effect=ZeroDivisionError
            ),
            self.assertLogs("apps.invoices.tasks", level="ERROR"),
        ):
            generate_export.apply(args=[export.id])

        export.refresh_from_db()
        self.assertEqual(export.status, InvoiceExport.Status.FAILED)
        self.assertFalse(export.file)
        self.assertEqual(self.stored_files(), [])

    def test_stored_file_is_removed_when_saving_the_export_fails(self):
        export = self.make_export()

        with (
            mock.patch.object(InvoiceExport, "save", side_effect=DatabaseError("lost")),
            self.assertLogs("apps.invoices.tasks", level="ERROR"),
        ):
            generate_export.apply(args=[export.id])

        self.assertEqual(self.stored_files(), [])

    def test_finished_export_is_not_generated_again(self):
        export = self.make_export(
            status=InvoiceExport.Status.COMPLETED,
            file=ContentFile(b"old", name="old.csv"),
            row_count=7,
        )

        with mock.patch("apps.invoices.tasks._write_export") as write_export:
            generate_export.apply(args=[export.id])

        write_export.assert_not_called()

    def test_unknown_export_is_ignored(self):
        self.assertTrue(generate_export.apply(args=[0]).successful())
