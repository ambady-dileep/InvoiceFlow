from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.imports.models import Upload
from apps.invoices.models import Invoice

User = get_user_model()


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
