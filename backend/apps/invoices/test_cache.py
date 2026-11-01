import tempfile
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import OperationalError
from django.test import override_settings
from django.urls import reverse
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework import status
from rest_framework.test import APITestCase

from apps.imports.models import Upload
from apps.imports.tasks import _save_batch, process_upload
from apps.invoices.cache import COUNT_FILTERS, COUNT_TIMEOUT, invalidate_invoice_counts
from apps.invoices.models import Invoice
from apps.invoices.views import InvoiceListView

User = get_user_model()

HEADER = "invoice_number,customer_name,amount,invoice_date,status\n"


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class InvoiceCountCacheTestCase(APITestCase):
    url = reverse("invoice-list")

    @classmethod
    def setUpTestData(cls):
        cls.merchant = User.objects.create_user("merchant", "m@example.com", "pass")
        cls.other_merchant = User.objects.create_user("other", "o@example.com", "pass")
        cls.upload = cls.make_upload(cls.merchant)
        cls.other_upload = cls.make_upload(cls.other_merchant)
        for number in range(30):
            cls.create(cls.merchant, cls.upload, f"INV-{number}", "paid")
        cls.create(cls.merchant, cls.upload, "INV-P", "pending")
        cls.create(cls.other_merchant, cls.other_upload, "OTHER-1", "paid")

    @classmethod
    def make_upload(cls, merchant):
        return Upload.objects.create(
            merchant=merchant,
            original_filename="invoices.csv",
            file="uploads/1/invoices.csv",
        )

    @classmethod
    def create(cls, merchant, upload, number, state):
        return Invoice.objects.create(
            merchant=merchant,
            upload=upload,
            row_number=upload.invoices.count() + 2,
            invoice_number=number,
            customer_name="Acme",
            amount=Decimal("1.00"),
            invoice_date=date(2026, 1, 1),
            status=state,
        )

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client.force_authenticate(self.merchant)

    def count(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["count"]


class CacheHitTests(InvoiceCountCacheTestCase):
    def test_second_request_skips_the_count_query(self):
        with self.assertNumQueries(2):
            self.assertEqual(self.count(), 31)
        with self.assertNumQueries(1):
            self.assertEqual(self.count(), 31)

    def test_zero_is_cached(self):
        with self.assertNumQueries(1):
            self.assertEqual(self.count(search="nothing"), 0)
        with self.assertNumQueries(0):
            self.assertEqual(self.count(search="nothing"), 0)

    def test_page_and_sort_changes_reuse_the_count(self):
        self.count()

        for params in ({"page": 2}, {"page_size": 10}, {"ordering": "-amount"}):
            with self.subTest(params=params), self.assertNumQueries(1):
                self.assertEqual(self.count(**params), 31)

    def test_each_filter_has_its_own_count(self):
        self.assertEqual(self.count(), 31)
        self.assertEqual(self.count(status="paid"), 30)
        self.assertEqual(self.count(status="pending"), 1)
        self.assertEqual(self.count(search="INV-P"), 1)
        self.assertEqual(self.count(status="paid", search="INV-P"), 0)

    def test_page_results_are_not_cached(self):
        self.count()
        Invoice.objects.filter(invoice_number="INV-P").update(customer_name="Renamed")

        response = self.client.get(self.url, {"search": "INV-P"})

        self.assertEqual(response.data["results"][0]["customer_name"], "Renamed")

    def test_count_filters_cover_every_list_filter(self):
        filters = [
            "search",
            *(
                "status" if lookup == "exact" else f"{field}__{lookup}"
                for field, lookups in InvoiceListView.filterset_fields.items()
                for lookup in lookups
            ),
        ]

        self.assertCountEqual(COUNT_FILTERS, filters)

    def test_counts_expire(self):
        with mock.patch(
            "apps.invoices.cache.cache.set", wraps=cache.set
        ) as cache_set:
            self.count()

        self.assertEqual(cache_set.call_args.args[2], COUNT_TIMEOUT)


class CacheIsolationTests(InvoiceCountCacheTestCase):
    def test_count_key_names_the_merchant(self):
        with mock.patch(
            "apps.invoices.cache.cache.set", wraps=cache.set
        ) as cache_set:
            self.count()

        key = cache_set.call_args.args[0]
        self.assertTrue(key.startswith(f"invoices:count:{self.merchant.id}:"), key)

    def test_merchants_never_share_a_count(self):
        self.assertEqual(self.count(status="paid"), 30)

        self.client.force_authenticate(self.other_merchant)

        self.assertEqual(self.count(status="paid"), 1)

    def test_cached_count_is_not_served_to_another_merchant(self):
        self.count()
        self.count()

        self.client.force_authenticate(self.other_merchant)

        with self.assertNumQueries(2):
            self.assertEqual(self.count(), 1)

    def test_invalidating_one_merchant_leaves_the_other_cached(self):
        self.count()
        self.client.force_authenticate(self.other_merchant)
        self.count()

        invalidate_invoice_counts(self.merchant.id)

        with self.assertNumQueries(1):
            self.assertEqual(self.count(), 1)


class CacheInvalidationTests(InvoiceCountCacheTestCase):
    def setUp(self):
        super().setUp()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        settings_override = override_settings(MEDIA_ROOT=media.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)

    def process(self, rows):
        upload = Upload.objects.create(
            merchant=self.merchant,
            original_filename="new.csv",
            file=ContentFile((HEADER + rows).encode(), name="new.csv"),
        )
        with self.captureOnCommitCallbacks(execute=True):
            process_upload.apply(args=[upload.id])

    def test_processed_upload_updates_the_count(self):
        self.assertEqual(self.count(), 31)

        self.process("NEW-1,Acme,1.00,2026-01-01,paid\nNEW-2,Acme,1.00,2026-01-01,paid\n")

        self.assertEqual(self.count(), 33)

    def test_filtered_counts_are_also_invalidated(self):
        self.assertEqual(self.count(status="pending"), 1)

        self.process("NEW-1,Acme,1.00,2026-01-01,pending\n")

        self.assertEqual(self.count(status="pending"), 2)

    def test_each_batch_invalidates_during_a_long_upload(self):
        rows = "".join(f"B-{n},Acme,1.00,2026-01-01,paid\n" for n in range(4))

        with (
            mock.patch("apps.imports.tasks.BATCH_SIZE", 2),
            mock.patch("apps.imports.tasks.invalidate_invoice_counts") as invalidate,
        ):
            self.process(rows)

        self.assertEqual(invalidate.call_count, 2)
        invalidate.assert_called_with(self.merchant.id)

    def test_upload_of_only_invalid_rows_keeps_the_cache(self):
        self.count()

        self.process("NEW-1,Acme,abc,2026-01-01,paid\n")

        with self.assertNumQueries(1):
            self.assertEqual(self.count(), 31)

    def test_rolled_back_batch_does_not_invalidate(self):
        upload = Upload.objects.create(
            merchant=self.merchant,
            original_filename="new.csv",
            file="uploads/1/new.csv",
            status=Upload.Status.PROCESSING,
        )
        row = (
            2,
            {
                "invoice_number": "NEW-1",
                "customer_name": "Acme",
                "amount": Decimal("1.00"),
                "invoice_date": date(2026, 1, 1),
                "status": "paid",
            },
            {},
        )

        with (
            mock.patch("apps.imports.tasks.invalidate_invoice_counts") as invalidate,
            mock.patch("apps.imports.tasks._update", side_effect=OperationalError),
            self.captureOnCommitCallbacks(execute=True),
            self.assertRaises(OperationalError),
        ):
            _save_batch(upload, 0, [row])

        invalidate.assert_not_called()


class CacheFailureTests(InvoiceCountCacheTestCase):
    def test_list_works_when_redis_is_unavailable(self):
        with (
            mock.patch(
                "apps.invoices.cache.cache.get_or_set",
                side_effect=RedisConnectionError("down"),
            ),
            self.assertLogs("apps.invoices.cache", level="WARNING"),
        ):
            self.assertEqual(self.count(), 31)

    def test_failed_store_still_returns_the_count(self):
        with (
            mock.patch(
                "apps.invoices.cache.cache.set", side_effect=RedisConnectionError("down")
            ),
            self.assertLogs("apps.invoices.cache", level="WARNING"),
        ):
            self.assertEqual(self.count(), 31)

    def test_failed_invalidation_does_not_raise(self):
        with (
            mock.patch(
                "apps.invoices.cache.cache.delete",
                side_effect=RedisConnectionError("down"),
            ),
            self.assertLogs("apps.invoices.cache", level="WARNING"),
        ):
            invalidate_invoice_counts(self.merchant.id)
