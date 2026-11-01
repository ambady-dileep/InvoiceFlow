from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITestCase
from rest_framework.views import APIView

from apps.imports.models import Upload
from apps.invoices.models import Invoice
from apps.invoices.validation import validate_row
from apps.invoices.views import InvoiceListView, StableOrderingFilter

User = get_user_model()


class ValidateRowTests(SimpleTestCase):
    def row(self, **overrides):
        return {
            "invoice_number": " INV-001 ",
            "customer_name": "Acme Traders",
            "amount": "1250.50",
            "invoice_date": "2026-01-15",
            "status": "Paid",
            **overrides,
        }

    def test_valid_row_is_cleaned(self):
        cleaned, errors = validate_row(self.row())

        self.assertEqual(errors, {})
        self.assertEqual(
            cleaned,
            {
                "invoice_number": "INV-001",
                "customer_name": "Acme Traders",
                "amount": Decimal("1250.50"),
                "invoice_date": date(2026, 1, 15),
                "status": "paid",
            },
        )

    def test_excel_values_are_accepted(self):
        cleaned, errors = validate_row(
            self.row(
                invoice_number=1001,
                amount=10.1,
                invoice_date=datetime(2026, 1, 15, 9, 30),
            )
        )

        self.assertEqual(errors, {})
        self.assertEqual(cleaned["invoice_number"], "1001")
        self.assertEqual(cleaned["amount"], Decimal("10.1"))
        self.assertEqual(cleaned["invoice_date"], date(2026, 1, 15))

    def test_every_error_in_a_row_is_reported(self):
        _, errors = validate_row({})

        self.assertEqual(
            errors,
            {
                column: ["This field is required."]
                for column in (
                    "invoice_number",
                    "customer_name",
                    "amount",
                    "invoice_date",
                    "status",
                )
            },
        )

    def test_invalid_values_are_rejected(self):
        cases = [
            ("invoice_number", "x" * 51),
            ("invoice_number", "   "),
            ("invoice_number", "INV\x00"),
            ("customer_name", "x" * 256),
            ("amount", "abc"),
            ("amount", "NaN"),
            ("amount", "Infinity"),
            ("amount", "-0.01"),
            ("amount", "10.123"),
            ("amount", "1000000000000.00"),
            ("amount", "1,000.00"),
            ("amount", True),
            ("invoice_date", "2026-02-30"),
            ("invoice_date", "15/01/2026"),
            ("invoice_date", 46037),
            ("status", "cancelled"),
        ]
        for column, value in cases:
            with self.subTest(column=column, value=value):
                _, errors = validate_row(self.row(**{column: value}))

                self.assertEqual(list(errors), [column])
                self.assertIsInstance(errors[column], list)

    def test_messages_never_repeat_the_cell_value(self):
        hostile = [
            "=cmd|' /C calc'!A0",
            "+1+1",
            "-1+1",
            "@SUM(1)",
            "x;=1+1",
            "x\t=1+1",
            "x\r\n=1+1",
        ]
        for value in hostile:
            for column in ("invoice_number", "customer_name", "amount", "invoice_date", "status"):
                with self.subTest(column=column, value=value):
                    _, errors = validate_row(self.row(**{column: value}))

                    for message in errors.get(column, []):
                        self.assertNotIn(value.strip(), message)
                        self.assertNotIn("=", message)

    def test_long_values_are_not_repeated(self):
        _, errors = validate_row(self.row(invoice_number="=" + "x" * 60))

        self.assertNotIn("xxx", errors["invoice_number"][0])

    def test_invalid_values_get_fixed_messages(self):
        cases = [
            ("amount", "abc", "Enter a valid number."),
            ("invoice_date", "15/01/2026", "Enter a valid date in YYYY-MM-DD format."),
            ("invoice_date", "2026-02-30", "Enter a valid date in YYYY-MM-DD format."),
            ("status", "cancelled", "Enter one of: pending, paid, overdue."),
        ]
        for column, value, message in cases:
            with self.subTest(column=column, value=value):
                _, errors = validate_row(self.row(**{column: value}))

                self.assertEqual(errors[column], [message])

    def test_zero_amount_is_valid(self):
        _, errors = validate_row(self.row(amount="0"))

        self.assertEqual(errors, {})


class InvoiceTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.merchant = User.objects.create_user("merchant", "m@example.com", "pass")
        cls.other_merchant = User.objects.create_user("other", "o@example.com", "pass")
        cls.upload = Upload.objects.create(
            merchant=cls.merchant, original_filename="invoices.csv"
        )
        cls.other_upload = Upload.objects.create(
            merchant=cls.other_merchant, original_filename="other.csv"
        )

    def build(self, **overrides):
        values = {
            "merchant": self.merchant,
            "upload": self.upload,
            "row_number": 1,
            "invoice_number": "INV-001",
            "customer_name": "Acme Traders",
            "amount": Decimal("1250.50"),
            "invoice_date": date(2026, 1, 15),
            "status": Invoice.Status.PENDING,
            **overrides,
        }
        return Invoice(**values)

    def assertRejectedByDatabase(self, invoice):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.bulk_create([invoice])


class InvoiceModelTests(InvoiceTestCase):
    def test_create_links_merchant_and_upload(self):
        invoice = self.build()
        invoice.full_clean()
        invoice.save()

        self.assertIn(invoice, self.merchant.invoices.all())
        self.assertIn(invoice, self.upload.invoices.all())

    def test_valid_statuses_are_accepted(self):
        for status in Invoice.Status.values:
            with self.subTest(status=status):
                self.build(status=status).full_clean()

    def test_invalid_status_is_rejected(self):
        with self.assertRaises(ValidationError) as context:
            self.build(status="cancelled").full_clean()

        self.assertIn("status", context.exception.message_dict)

    def test_required_fields_are_validated(self):
        with self.assertRaises(ValidationError) as context:
            Invoice().full_clean()

        for field in (
            "merchant",
            "upload",
            "row_number",
            "invoice_number",
            "customer_name",
            "amount",
            "invoice_date",
            "status",
        ):
            with self.subTest(field=field):
                self.assertIn(field, context.exception.message_dict)

    def test_amount_keeps_exact_decimal_value(self):
        for amount in ("0.01", "1250.50", "999999999999.99"):
            with self.subTest(amount=amount):
                invoice = self.build(
                    invoice_number=amount,
                    row_number=Invoice.objects.count() + 1,
                    amount=Decimal(amount),
                )
                invoice.save()

                invoice.refresh_from_db()
                self.assertEqual(invoice.amount, Decimal(amount))

    def test_invalid_amounts_are_rejected(self):
        for amount in ("10.123", "1000000000000.00", "-0.01"):
            with self.subTest(amount=amount):
                with self.assertRaises(ValidationError) as context:
                    self.build(amount=Decimal(amount)).full_clean()

                self.assertIn("amount", context.exception.message_dict)

    def test_deleting_upload_deletes_its_invoices(self):
        self.build().save()

        self.upload.delete()

        self.assertFalse(Invoice.objects.exists())

    def test_deleting_merchant_deletes_uploads_and_invoices(self):
        self.build().save()

        self.merchant.delete()

        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(Upload.objects.filter(pk=self.upload.pk).exists())


class InvoiceConstraintTests(InvoiceTestCase):
    def test_invoice_cannot_use_another_merchants_upload(self):
        self.assertRejectedByDatabase(self.build(upload=self.other_upload))

    def test_duplicate_invoice_number_is_rejected_across_uploads(self):
        self.build().save()
        later_upload = Upload.objects.create(
            merchant=self.merchant, original_filename="later.csv"
        )

        self.assertRejectedByDatabase(self.build(upload=later_upload))

    def test_merchants_can_share_an_invoice_number(self):
        self.build().save()

        self.build(merchant=self.other_merchant, upload=self.other_upload).save()

        self.assertEqual(Invoice.objects.count(), 2)

    def test_duplicate_upload_row_is_rejected(self):
        self.build().save()

        self.assertRejectedByDatabase(self.build(invoice_number="INV-002"))

    def test_row_number_can_repeat_across_uploads(self):
        self.build().save()
        later_upload = Upload.objects.create(
            merchant=self.merchant, original_filename="later.csv"
        )

        self.build(upload=later_upload, invoice_number="INV-002").save()

        self.assertEqual(Invoice.objects.count(), 2)

    def test_negative_amount_is_rejected_by_database(self):
        self.assertRejectedByDatabase(self.build(amount=Decimal("-0.01")))

    def test_invalid_status_is_rejected_by_database(self):
        self.assertRejectedByDatabase(self.build(status="cancelled"))


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
)
class InvoiceListTestCase(APITestCase):
    url = reverse("invoice-list")

    @classmethod
    def setUpTestData(cls):
        cls.merchant = User.objects.create_user("merchant", "m@example.com", "pass")
        cls.other_merchant = User.objects.create_user("other", "o@example.com", "pass")
        cls.upload = cls.make_upload(cls.merchant)
        cls.other_upload = cls.make_upload(cls.other_merchant)
        cls.create("INV-001", "Acme Traders", "100.00", date(2026, 1, 10), "paid")
        cls.create("INV-002", "Globex", "250.50", date(2026, 1, 20), "pending")
        cls.create("INV-003", "acme retail", "999.99", date(2026, 2, 5), "overdue")
        cls.create("INV-004", "Initech", "250.50", date(2026, 1, 20), "paid")
        Invoice.objects.create(
            merchant=cls.other_merchant,
            upload=cls.other_upload,
            row_number=2,
            invoice_number="OTHER-1",
            customer_name="Acme Hidden",
            amount=Decimal("500.00"),
            invoice_date=date(2026, 1, 15),
            status="paid",
        )

    @classmethod
    def make_upload(cls, merchant):
        return Upload.objects.create(
            merchant=merchant,
            original_filename="invoices.csv",
            file="uploads/1/invoices.csv",
        )

    @classmethod
    def create(cls, number, customer, amount, invoice_date, invoice_status):
        return Invoice.objects.create(
            merchant=cls.merchant,
            upload=cls.upload,
            row_number=Invoice.objects.filter(upload=cls.upload).count() + 2,
            invoice_number=number,
            customer_name=customer,
            amount=Decimal(amount),
            invoice_date=invoice_date,
            status=invoice_status,
        )

    def setUp(self):
        self.client.force_authenticate(self.merchant)

    def get_list(self, **params):
        return self.client.get(self.url, params)

    def numbers(self, **params):
        response = self.get_list(**params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [invoice["invoice_number"] for invoice in response.data["results"]]


class InvoiceListResponseTests(InvoiceListTestCase):
    def test_response_is_paginated_with_invoice_fields(self):
        response = self.get_list(search="INV-001")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data), {"count", "next", "previous", "results"}
        )
        invoice = Invoice.objects.get(invoice_number="INV-001")
        self.assertEqual(
            response.json()["results"],
            [
                {
                    "id": invoice.id,
                    "invoice_number": "INV-001",
                    "customer_name": "Acme Traders",
                    "amount": "100.00",
                    "invoice_date": "2026-01-10",
                    "status": "paid",
                }
            ],
        )

    def test_no_matches_return_an_empty_page(self):
        response = self.get_list(search="nothing-matches-this")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])

    def test_write_methods_are_not_allowed(self):
        for method in (self.client.post, self.client.put, self.client.delete):
            with self.subTest(method=method.__name__):
                response = method(self.url, {})

                self.assertEqual(
                    response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED
                )


class InvoiceFilterTests(InvoiceListTestCase):
    def test_status_filter(self):
        self.assertCountEqual(self.numbers(status="paid"), ["INV-001", "INV-004"])

    def test_date_range_includes_both_ends(self):
        numbers = self.numbers(
            invoice_date__gte="2026-01-10", invoice_date__lte="2026-01-20"
        )

        self.assertCountEqual(numbers, ["INV-001", "INV-002", "INV-004"])

    def test_date_filters_work_alone(self):
        self.assertEqual(self.numbers(invoice_date__gte="2026-02-01"), ["INV-003"])
        self.assertEqual(self.numbers(invoice_date__lte="2026-01-10"), ["INV-001"])

    def test_amount_range_includes_both_ends(self):
        numbers = self.numbers(amount__gte="100.00", amount__lte="250.50")

        self.assertCountEqual(numbers, ["INV-001", "INV-002", "INV-004"])

    def test_amount_filters_work_alone(self):
        self.assertEqual(self.numbers(amount__gte="999.99"), ["INV-003"])
        self.assertEqual(self.numbers(amount__lte="100"), ["INV-001"])

    def test_filters_search_and_ordering_combine(self):
        numbers = self.numbers(
            search="in",
            status="paid",
            amount__gte="100",
            invoice_date__lte="2026-01-31",
            ordering="-amount",
        )

        self.assertEqual(numbers, ["INV-004", "INV-001"])

    def test_reversed_range_returns_nothing(self):
        self.assertEqual(self.numbers(amount__gte="500", amount__lte="100"), [])

    def test_invalid_filter_values_are_rejected(self):
        cases = [
            ("status", "cancelled"),
            ("invoice_date__gte", "15/01/2026"),
            ("invoice_date__lte", "2026-02-30"),
            ("amount__gte", "abc"),
            ("amount__lte", "NaN"),
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value):
                response = self.get_list(**{name: value})

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(name, response.data)

    def test_blank_filters_are_ignored(self):
        self.assertEqual(len(self.numbers(status="", amount__gte="")), 4)


class InvoiceSearchTests(InvoiceListTestCase):
    def test_search_matches_part_of_an_invoice_number(self):
        self.assertEqual(self.numbers(search="-003"), ["INV-003"])

    def test_search_matches_part_of_a_customer_name(self):
        self.assertEqual(self.numbers(search="lobe"), ["INV-002"])

    def test_search_ignores_case(self):
        self.assertCountEqual(self.numbers(search="ACME"), ["INV-001", "INV-003"])

    def test_multiple_terms_must_all_match(self):
        self.assertEqual(self.numbers(search="acme retail"), ["INV-003"])

    def test_search_does_not_cover_other_fields(self):
        self.assertEqual(self.numbers(search="overdue"), [])

    def test_percent_and_underscore_are_matched_literally(self):
        self.create("100%", "Percent", "1.00", date(2026, 1, 1), "paid")
        self.create("A_B", "Underscore", "1.00", date(2026, 1, 1), "paid")
        self.create("AXB", "Plain", "1.00", date(2026, 1, 1), "paid")

        self.assertEqual(self.numbers(search="%"), ["100%"])
        self.assertEqual(self.numbers(search="_"), ["A_B"])


class InvoiceOrderingTests(InvoiceListTestCase):
    def test_default_is_newest_first_with_newest_id_breaking_ties(self):
        self.assertEqual(
            self.numbers(), ["INV-003", "INV-004", "INV-002", "INV-001"]
        )

    def test_every_field_sorts_both_ways(self):
        cases = {
            "invoice_number": ["INV-001", "INV-002", "INV-003", "INV-004"],
            "-invoice_number": ["INV-004", "INV-003", "INV-002", "INV-001"],
            "amount": ["INV-001", "INV-004", "INV-002", "INV-003"],
            "-amount": ["INV-003", "INV-004", "INV-002", "INV-001"],
            "invoice_date": ["INV-001", "INV-004", "INV-002", "INV-003"],
            "-invoice_date": ["INV-003", "INV-004", "INV-002", "INV-001"],
            "status": ["INV-003", "INV-004", "INV-001", "INV-002"],
            "-status": ["INV-002", "INV-004", "INV-001", "INV-003"],
        }
        for ordering, expected in cases.items():
            with self.subTest(ordering=ordering):
                self.assertEqual(self.numbers(ordering=ordering), expected)

    def test_customer_name_sorts_both_ways(self):
        ascending = self.numbers(ordering="customer_name")

        self.assertEqual(len(ascending), 4)
        self.assertEqual(self.numbers(ordering="-customer_name"), ascending[::-1])

    def test_several_fields_can_be_combined(self):
        self.assertEqual(
            self.numbers(ordering="-amount,invoice_number"),
            ["INV-003", "INV-002", "INV-004", "INV-001"],
        )

    def test_every_ordering_ends_with_the_tie_break(self):
        fields = InvoiceListView.ordering_fields
        for ordering in [*fields, *[f"-{field}" for field in fields], None]:
            with self.subTest(ordering=ordering):
                params = {"ordering": ordering} if ordering else {}
                request = APIView().initialize_request(
                    APIRequestFactory().get(self.url, params)
                )

                queryset = StableOrderingFilter().filter_queryset(
                    request, Invoice.objects.all(), InvoiceListView()
                )

                self.assertEqual(queryset.query.order_by[-1], "-id")

    def test_tied_rows_are_not_repeated_or_lost_across_pages(self):
        for number in range(40):
            self.create(f"TIE-{number:03d}", "Tied", "7.00", date(2026, 3, 1), "paid")
        for field in InvoiceListView.ordering_fields:
            for ordering in (field, f"-{field}"):
                with self.subTest(ordering=ordering):
                    seen = []
                    for page in (1, 2, 3, 4):
                        seen += self.numbers(
                            search="TIE", ordering=ordering, page=page, page_size=10
                        )

                    self.assertEqual(len(set(seen)), 40)

    def test_unknown_ordering_falls_back_to_the_default(self):
        self.assertEqual(self.numbers(ordering="nope")[0], "INV-003")

    def test_private_columns_cannot_be_used_for_ordering(self):
        default = self.numbers()

        for field in ("merchant", "merchant__password", "upload", "row_number", "id"):
            with self.subTest(field=field):
                self.assertEqual(self.numbers(ordering=field), default)


class InvoicePaginationTests(InvoiceListTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for number in range(146):
            cls.create(f"PAGE-{number:03d}", "Paged", "1.00", date(2025, 1, 1), "paid")

    def test_default_page_size_is_25(self):
        response = self.get_list()

        self.assertEqual(response.data["count"], 150)
        self.assertEqual(len(response.data["results"]), 25)
        self.assertIsNone(response.data["previous"])

    def test_page_size_can_be_chosen(self):
        self.assertEqual(len(self.numbers(page_size=10)), 10)

    def test_page_size_is_capped_at_100(self):
        self.assertEqual(len(self.numbers(page_size=1000)), 100)

    def test_invalid_page_size_uses_the_default(self):
        for value in ("abc", "0", "-5"):
            with self.subTest(value=value):
                self.assertEqual(len(self.numbers(page_size=value)), 25)

    def test_last_page_has_no_next_link(self):
        response = self.get_list(page=2, page_size=100)

        self.assertEqual(len(response.data["results"]), 50)
        self.assertIsNone(response.data["next"])
        self.assertIsNotNone(response.data["previous"])

    def test_links_keep_the_query_parameters(self):
        response = self.get_list(search="PAGE", status="paid", ordering="amount")

        self.assertIn("search=PAGE", response.data["next"])
        self.assertIn("status=paid", response.data["next"])
        self.assertIn("ordering=amount", response.data["next"])
        self.assertIn("page=2", response.data["next"])

    def test_invalid_pages_return_not_found(self):
        for value in ("0", "abc", "9999"):
            with self.subTest(value=value):
                self.assertEqual(
                    self.get_list(page=value).status_code, status.HTTP_404_NOT_FOUND
                )

    def test_a_page_uses_only_a_count_and_a_select(self):
        with self.assertNumQueries(2):
            self.get_list(page_size=1)
        with self.assertNumQueries(2):
            self.get_list(page_size=100)

    def test_filtered_page_uses_only_a_count_and_a_select(self):
        with self.assertNumQueries(2):
            self.get_list(search="PAGE", status="paid", ordering="-amount")


class InvoiceIsolationTests(InvoiceListTestCase):
    def test_unauthenticated_request_is_rejected(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.get_list().status_code, status.HTTP_401_UNAUTHORIZED)

    def test_only_the_merchants_invoices_are_listed(self):
        response = self.get_list()

        self.assertEqual(response.data["count"], 4)
        self.assertNotIn("OTHER-1", str(response.data))

    def test_search_filters_and_count_cannot_reach_other_merchants(self):
        cases = [
            {"search": "OTHER-1"},
            {"search": "Hidden"},
            {"amount__gte": "500", "amount__lte": "500"},
            {"invoice_date__gte": "2026-01-15", "invoice_date__lte": "2026-01-15"},
        ]
        for params in cases:
            with self.subTest(params=params):
                response = self.get_list(**params)

                self.assertEqual(response.data["count"], 0)

    def test_merchant_parameter_cannot_switch_merchants(self):
        response = self.get_list(merchant=self.other_merchant.id)

        self.assertEqual(response.data["count"], 4)

    def test_each_merchant_sees_their_own_invoices(self):
        self.client.force_authenticate(self.other_merchant)

        self.assertEqual(self.numbers(), ["OTHER-1"])

    def test_private_fields_are_not_exposed(self):
        invoice = self.get_list().data["results"][0]

        for field in ("merchant", "upload", "row_number"):
            self.assertNotIn(field, invoice)
