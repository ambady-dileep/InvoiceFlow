from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from apps.imports.models import Upload
from apps.invoices.models import Invoice
from apps.invoices.validation import validate_row

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
