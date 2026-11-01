import csv
import io
import logging
import tempfile
import threading

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.core.files import File
from django.db import DatabaseError, connections
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerError

from apps.imports.tasks import RETRY_DELAY, TRANSIENT_ERRORS
from apps.imports.validators import REQUIRED_COLUMNS
from apps.imports.views import safe_cell
from apps.invoices.models import InvoiceExport
from apps.invoices.views import filtered_invoices

logger = logging.getLogger(__name__)


def queue_export(export_id):
    """Publish from a thread, because a failed Redis DNS lookup takes about 4s."""
    threading.Thread(target=_publish_in_thread, args=(export_id,)).start()


def _publish_in_thread(export_id):
    try:
        _publish(export_id)
    finally:
        connections.close_all()


def _publish(export_id):
    try:
        generate_export.delay(export_id)
    except BrokerError:
        logger.exception("Could not queue export %s", export_id)
        _fail(export_id, "The export could not be queued.")


@shared_task(bind=True, max_retries=3)
def generate_export(self, export_id):
    try:
        claimed = InvoiceExport.objects.filter(
            pk=export_id,
            status__in=[InvoiceExport.Status.PENDING, InvoiceExport.Status.PROCESSING],
        ).update(
            status=InvoiceExport.Status.PROCESSING,
            failure_reason="",
            updated_at=timezone.now(),
        )
        if claimed:
            _write_export(InvoiceExport.objects.get(pk=export_id))
    except SoftTimeLimitExceeded:
        logger.exception("Export %s timed out", export_id)
        _fail(export_id, "The export took too long.")
    except TRANSIENT_ERRORS as error:
        if self.request.retries >= self.max_retries:
            logger.exception("Export %s failed after retries", export_id)
            _fail(export_id, "The export could not be created. Please try again.")
            return
        raise self.retry(exc=error, countdown=RETRY_DELAY * 2**self.request.retries)
    except Exception:
        logger.exception("Export %s failed unexpectedly", export_id)
        _fail(export_id, "The export could not be created.")


def _write_export(export):
    """Write to a temporary file, so a partial export can never be downloaded."""
    invoices = filtered_invoices(export.merchant_id, export.query)
    with tempfile.TemporaryFile() as temporary:
        # The byte order mark makes Excel read the file as UTF-8.
        text = io.TextIOWrapper(temporary, encoding="utf-8-sig", newline="")
        writer = csv.writer(text)
        writer.writerow(REQUIRED_COLUMNS)
        row_count = 0
        rows = invoices.values_list(*REQUIRED_COLUMNS).iterator(chunk_size=2000)
        for number, customer, amount, invoice_date, status in rows:
            writer.writerow(
                [safe_cell(number), safe_cell(customer), amount, invoice_date, status]
            )
            row_count += 1
        text.flush()
        text.detach()
        temporary.seek(0)
        export.file.save("export.csv", File(temporary), save=False)

    export.status = InvoiceExport.Status.COMPLETED
    export.row_count = row_count
    try:
        export.save(update_fields=["file", "status", "row_count", "updated_at"])
    except DatabaseError:
        # A retry writes a new file, so this one would never be used.
        export.file.delete(save=False)
        raise


def _fail(export_id, reason):
    InvoiceExport.objects.filter(pk=export_id).update(
        status=InvoiceExport.Status.FAILED,
        failure_reason=reason,
        updated_at=timezone.now(),
    )
