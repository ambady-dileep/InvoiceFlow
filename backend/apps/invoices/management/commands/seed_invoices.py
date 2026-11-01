import csv
import random
from datetime import date, timedelta
from decimal import Decimal
from functools import partial
from itertools import batched
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.imports.models import Upload
from apps.imports.tasks import BATCH_SIZE
from apps.imports.validators import REQUIRED_COLUMNS
from apps.invoices.cache import invalidate_invoice_counts
from apps.invoices.models import Invoice

MIN_ROWS = 10_000

NAMES = ("Acme", "Globex", "Initech", "Umbrella", "Stark", "Wayne", "Hooli", "Soylent")
KINDS = ("Traders", "Logistics", "Retail", "Foods", "Textiles", "Systems", "Exports")
LATEST_DATE = date(2026, 1, 1)
DATE_RANGE_DAYS = 730
STATUSES = (Invoice.Status.PAID, Invoice.Status.PENDING, Invoice.Status.OVERDUE)
STATUS_WEIGHTS = (60, 30, 10)


def generate_rows(rows, seed, prefix):
    """Yield valid invoice rows one at a time. The same seed gives the same values."""
    rng = random.Random(seed)
    for number in range(1, rows + 1):
        yield {
            "invoice_number": f"{prefix}-{number:06d}",
            "customer_name": f"{rng.choice(NAMES)} {rng.choice(KINDS)} {rng.randint(1, 500)}",
            "amount": Decimal(rng.randint(100, 5_000_000)).scaleb(-2),
            "invoice_date": LATEST_DATE - timedelta(days=rng.randrange(DATE_RANGE_DAYS)),
            "status": rng.choices(STATUSES, STATUS_WEIGHTS)[0].value,
        }


class Command(BaseCommand):
    help = "Generate a test CSV file, or add seeded invoices to a merchant."

    def add_arguments(self, parser):
        parser.add_argument("--rows", type=int, default=MIN_ROWS)
        parser.add_argument("--seed", type=int, default=42)
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--output", help="CSV file to create.")
        target.add_argument("--merchant", help="Username to add invoices to.")

    def handle(self, *args, rows, seed, output, merchant, **options):
        if not MIN_ROWS <= rows <= settings.MAX_UPLOAD_ROWS:
            raise CommandError(
                f"--rows must be between {MIN_ROWS:,} and {settings.MAX_UPLOAD_ROWS:,}."
            )
        if output:
            self.write_file(Path(output), rows, seed)
            self.stdout.write(f"Wrote {rows:,} rows to {output}.")
        else:
            upload = self.insert_invoices(merchant, rows, seed)
            self.stdout.write(f"Added {rows:,} invoices to {merchant} as upload {upload.id}.")

    def write_file(self, path, rows, seed):
        try:
            # Exclusive mode refuses to replace an existing file.
            with path.open("x", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, REQUIRED_COLUMNS)
                writer.writeheader()
                writer.writerows(generate_rows(rows, seed, f"SEED-{seed}"))
        except FileExistsError:
            raise CommandError(f"{path} already exists. It was not changed.")
        except OSError as error:
            raise CommandError(f"Could not write {path}: {error}")

    def insert_invoices(self, username, rows, seed):
        User = get_user_model()
        try:
            merchant = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"Merchant {username} does not exist.")

        with transaction.atomic():
            upload = Upload.objects.create(
                merchant=merchant,
                original_filename=f"seed-{seed}.csv",
                status=Upload.Status.COMPLETED,
                total_rows=rows,
                processed_rows=rows,
                successful_rows=rows,
            )
            # The upload ID keeps numbers unique when the command runs again.
            generated = generate_rows(rows, seed, f"SEED-{upload.id}")
            for start, batch in enumerate(batched(generated, BATCH_SIZE)):
                Invoice.objects.bulk_create(
                    Invoice(
                        merchant=merchant,
                        upload=upload,
                        row_number=start * BATCH_SIZE + offset + 2,
                        **row,
                    )
                    for offset, row in enumerate(batch)
                )
            transaction.on_commit(partial(invalidate_invoice_counts, merchant.id))
        return upload
