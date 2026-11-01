import csv
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.db import DatabaseError
from django.test import TestCase, override_settings

from apps.imports.models import Upload
from apps.imports.tasks import process_upload
from apps.imports.validators import REQUIRED_COLUMNS, validate_upload_file
from apps.invoices.management.commands import seed_invoices
from apps.invoices.models import Invoice
from apps.invoices.validation import validate_row

User = get_user_model()

SEED_MODULE = "apps.invoices.management.commands.seed_invoices"


class SeedTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.merchant = User.objects.create_user("merchant", "m@example.com", "pass")
        cls.other_merchant = User.objects.create_user("other", "o@example.com", "pass")

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        minimum = mock.patch(f"{SEED_MODULE}.MIN_ROWS", 1)
        minimum.start()
        self.addCleanup(minimum.stop)

    def seed(self, **options):
        call_command("seed_invoices", stdout=StringIO(), **options)

    def read_rows(self, path):
        with path.open(encoding="utf-8", newline="") as file:
            return list(csv.DictReader(file))


class SeedValidationTests(SeedTestCase):
    def test_a_target_is_required(self):
        with self.assertRaises(CommandError):
            self.seed(rows=10)

    def test_file_and_merchant_cannot_be_combined(self):
        with self.assertRaises(CommandError):
            call_command(
                "seed_invoices",
                "--merchant=merchant",
                f"--output={self.folder / 'a.csv'}",
                stdout=StringIO(),
            )

    def test_unknown_merchant_is_rejected(self):
        with self.assertRaisesMessage(CommandError, "Merchant nobody does not exist."):
            self.seed(rows=10, merchant="nobody")

        self.assertFalse(Upload.objects.exists())

    def test_rows_must_be_inside_the_assignment_range(self):
        with mock.patch(f"{SEED_MODULE}.MIN_ROWS", 10_000):
            for rows in (0, -1, 9_999, 100_001):
                with self.subTest(rows=rows):
                    with self.assertRaisesMessage(CommandError, "--rows must be between"):
                        self.seed(rows=rows, merchant="merchant")

        self.assertFalse(Invoice.objects.exists())

    def test_range_limits_are_accepted(self):
        with mock.patch(f"{SEED_MODULE}.MIN_ROWS", 10_000):
            with mock.patch(f"{SEED_MODULE}.Command.write_file") as write_file:
                for rows in (10_000, 100_000):
                    self.seed(rows=rows, output="unused.csv")

        self.assertEqual(write_file.call_count, 2)


class SeedFileTests(SeedTestCase):
    def test_file_has_the_upload_columns_and_requested_rows(self):
        path = self.folder / "invoices.csv"

        self.seed(rows=25, output=str(path))

        rows = self.read_rows(path)
        self.assertEqual(len(rows), 25)
        self.assertEqual(list(rows[0]), list(REQUIRED_COLUMNS))

    def test_same_seed_gives_an_identical_file(self):
        first, second = self.folder / "a.csv", self.folder / "b.csv"

        self.seed(rows=50, seed=7, output=str(first))
        self.seed(rows=50, seed=7, output=str(second))

        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_different_seed_gives_different_data(self):
        first, second = self.folder / "a.csv", self.folder / "b.csv"

        self.seed(rows=50, seed=1, output=str(first))
        self.seed(rows=50, seed=2, output=str(second))

        self.assertNotEqual(first.read_bytes(), second.read_bytes())

    def test_existing_file_is_not_overwritten(self):
        path = self.folder / "invoices.csv"
        path.write_text("keep me")

        with self.assertRaisesMessage(CommandError, "already exists"):
            self.seed(rows=10, output=str(path))

        self.assertEqual(path.read_text(), "keep me")

    def test_unwritable_path_gives_a_clear_error(self):
        with self.assertRaisesMessage(CommandError, "Could not write"):
            self.seed(rows=10, output=str(self.folder / "missing" / "invoices.csv"))

    def test_every_row_passes_invoice_validation(self):
        path = self.folder / "invoices.csv"

        self.seed(rows=300, output=str(path))

        for row in self.read_rows(path):
            self.assertEqual(validate_row(row)[1], {})

    def test_data_varies_like_real_invoices(self):
        path = self.folder / "invoices.csv"

        self.seed(rows=300, output=str(path))

        rows = self.read_rows(path)
        self.assertEqual({row["status"] for row in rows}, {"paid", "pending", "overdue"})
        self.assertGreater(len({row["customer_name"] for row in rows}), 50)
        self.assertGreater(len({row["invoice_date"] for row in rows}), 50)
        self.assertEqual(len({row["invoice_number"] for row in rows}), 300)

    def test_file_passes_upload_validation_and_processing(self):
        path = self.folder / "invoices.csv"
        self.seed(rows=30, output=str(path))
        content = path.read_bytes()

        validate_upload_file(
            SimpleUploadedFile("invoices.csv", content, content_type="text/csv")
        )
        with override_settings(MEDIA_ROOT=str(self.folder)):
            upload = Upload.objects.create(
                merchant=self.merchant,
                original_filename="invoices.csv",
                file=ContentFile(content, name="invoices.csv"),
            )
            process_upload.apply(args=[upload.id])

        upload.refresh_from_db()
        self.assertEqual(upload.status, Upload.Status.COMPLETED)
        self.assertEqual(upload.successful_rows, 30)
        self.assertEqual(upload.failed_rows, 0)


class SeedDatabaseTests(SeedTestCase):
    def test_invoices_belong_to_the_merchant_and_one_upload(self):
        self.seed(rows=25, merchant="merchant")

        upload = Upload.objects.get()
        self.assertEqual(upload.merchant, self.merchant)
        self.assertEqual(upload.invoices.count(), 25)
        self.assertEqual(self.merchant.invoices.count(), 25)
        self.assertFalse(self.other_merchant.invoices.exists())

    def test_upload_counters_describe_a_finished_upload(self):
        self.seed(rows=25, merchant="merchant")

        upload = Upload.objects.get()
        self.assertEqual(upload.status, Upload.Status.COMPLETED)
        self.assertEqual(upload.total_rows, 25)
        self.assertEqual(upload.processed_rows, 25)
        self.assertEqual(upload.successful_rows, 25)
        self.assertEqual(upload.failed_rows, 0)

    def test_stored_invoices_pass_model_validation(self):
        self.seed(rows=20, merchant="merchant")

        for invoice in Invoice.objects.all():
            invoice.full_clean()

    def test_row_numbers_stay_unique_across_batches(self):
        with mock.patch(f"{SEED_MODULE}.BATCH_SIZE", 7):
            self.seed(rows=25, merchant="merchant")

        numbers = sorted(Invoice.objects.values_list("row_number", flat=True))
        self.assertEqual(numbers, list(range(2, 27)))

    def test_running_again_adds_rows_without_touching_existing_data(self):
        self.seed(rows=10, merchant="merchant")
        existing = list(Invoice.objects.order_by("id").values())

        self.seed(rows=10, merchant="merchant")

        self.assertEqual(Upload.objects.count(), 2)
        self.assertEqual(Invoice.objects.count(), 20)
        self.assertEqual(list(Invoice.objects.order_by("id").values()[:10]), existing)

    def test_same_seed_repeats_the_values_under_new_numbers(self):
        values = ("customer_name", "amount", "invoice_date", "status")
        self.seed(rows=10, seed=5, merchant="merchant")
        self.seed(rows=10, seed=5, merchant="merchant")

        first, second = Upload.objects.order_by("id")

        self.assertEqual(
            list(first.invoices.order_by("row_number").values_list(*values)),
            list(second.invoices.order_by("row_number").values_list(*values)),
        )

    def test_rows_are_inserted_in_batches(self):
        with (
            mock.patch(f"{SEED_MODULE}.BATCH_SIZE", 10),
            mock.patch.object(
                Invoice.objects, "bulk_create", wraps=Invoice.objects.bulk_create
            ) as bulk_create,
            mock.patch.object(Invoice, "save") as save,
        ):
            self.seed(rows=25, merchant="merchant")

        self.assertEqual(bulk_create.call_count, 3)
        save.assert_not_called()

    def test_failure_rolls_everything_back(self):
        calls = []
        real_bulk_create = Invoice.objects.bulk_create

        def fail_on_second_batch(objects):
            calls.append(1)
            if len(calls) == 2:
                raise DatabaseError("connection lost")
            return real_bulk_create(objects)

        with (
            mock.patch(f"{SEED_MODULE}.BATCH_SIZE", 10),
            mock.patch.object(
                Invoice.objects, "bulk_create", side_effect=fail_on_second_batch
            ),
            mock.patch(f"{SEED_MODULE}.invalidate_invoice_counts") as invalidate,
            self.captureOnCommitCallbacks(execute=True),
            self.assertRaises(DatabaseError),
        ):
            self.seed(rows=25, merchant="merchant")

        self.assertFalse(Upload.objects.exists())
        self.assertFalse(Invoice.objects.exists())
        invalidate.assert_not_called()

    def test_no_background_task_is_queued(self):
        with mock.patch("apps.imports.tasks.process_upload.delay") as delay:
            self.seed(rows=10, merchant="merchant")

        delay.assert_not_called()

    def test_cache_is_invalidated_once_after_commit(self):
        with mock.patch(f"{SEED_MODULE}.invalidate_invoice_counts") as invalidate:
            with self.captureOnCommitCallbacks(execute=True):
                self.seed(rows=25, merchant="merchant")

        invalidate.assert_called_once_with(self.merchant.id)

    def test_generator_yields_rows_lazily(self):
        rows = seed_invoices.generate_rows(100_000, 1, "SEED")

        self.assertEqual(next(rows)["invoice_number"], "SEED-000001")
