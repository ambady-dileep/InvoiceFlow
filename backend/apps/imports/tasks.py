import logging
import threading
from itertools import batched, islice
from pathlib import Path

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import InterfaceError, OperationalError, connections, transaction
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerError

from apps.imports.models import Upload, UploadRowError
from apps.imports.parsing import UploadFileError, iter_rows
from apps.invoices.models import Invoice
from apps.invoices.validation import validate_row

logger = logging.getLogger(__name__)

User = get_user_model()

BATCH_SIZE = 1000
RETRY_DELAY = 30
TRANSIENT_ERRORS = (OperationalError, InterfaceError, OSError)
DUPLICATE_MESSAGE = "This invoice number already exists."


def queue_upload(upload_id):
    """Publish from a thread, because a failed Redis DNS lookup takes about 4s."""
    threading.Thread(target=_publish_in_thread, args=(upload_id,)).start()


def _publish_in_thread(upload_id):
    try:
        _publish(upload_id)
    finally:
        connections.close_all()


def _publish(upload_id):
    try:
        process_upload.delay(upload_id)
    except BrokerError:
        logger.exception("Could not queue upload %s", upload_id)
        _fail(upload_id, "The upload could not be queued for processing.")


@shared_task(bind=True, max_retries=3)
def process_upload(self, upload_id):
    try:
        upload = _claim(upload_id)
        if upload is None:
            return
        _update(upload_id, total_rows=_count_rows(upload))
        if _process_rows(upload):
            _update(upload_id, status=Upload.Status.COMPLETED)
    except UploadFileError as error:
        _fail(upload_id, str(error))
    except FileNotFoundError:
        logger.exception("File missing for upload %s", upload_id)
        _fail(upload_id, "The uploaded file could not be found.")
    except SoftTimeLimitExceeded:
        logger.exception("Upload %s timed out", upload_id)
        _fail(upload_id, "Processing took too long.")
    except TRANSIENT_ERRORS as error:
        if self.request.retries >= self.max_retries:
            logger.exception("Upload %s failed after retries", upload_id)
            _fail(upload_id, "The upload could not be processed. Please try again.")
            return
        raise self.retry(exc=error, countdown=RETRY_DELAY * 2**self.request.retries)
    except Exception:
        logger.exception("Upload %s failed unexpectedly", upload_id)
        _fail(upload_id, "The upload could not be processed.")


def _claim(upload_id):
    """Start or resume an unfinished upload. Finished uploads are left alone."""
    claimed = Upload.objects.filter(
        pk=upload_id,
        status__in=[Upload.Status.PENDING, Upload.Status.PROCESSING],
    ).update(
        status=Upload.Status.PROCESSING,
        failure_reason="",
        updated_at=timezone.now(),
    )
    return Upload.objects.get(pk=upload_id) if claimed else None


def _count_rows(upload):
    total = 0
    for _ in _rows(upload):
        total += 1
        if total > settings.MAX_UPLOAD_ROWS:
            raise UploadFileError(
                f"The file has more than {settings.MAX_UPLOAD_ROWS:,} rows."
            )
    if not total:
        raise UploadFileError("The file has no invoice rows.")
    return total


def _process_rows(upload):
    """Save rows in batches, resuming after the last committed batch.

    Rows are read and validated before each transaction, so no database lock is
    held while parsing. Returns False if another delivery has taken over.
    """
    processed = upload.processed_rows
    for batch in batched(islice(_rows(upload), processed, None), BATCH_SIZE):
        validated = [(number, *validate_row(row)) for number, row in batch]
        if not _save_batch(upload, processed, validated):
            return False
        processed += len(batch)
    return True


def _save_batch(upload, processed, rows):
    """Commit a batch's invoices, row errors, and counters together."""
    with transaction.atomic():
        current = Upload.objects.select_for_update(no_key=True).get(pk=upload.pk)
        if (
            current.status != Upload.Status.PROCESSING
            or current.processed_rows != processed
        ):
            return False

        # Concurrent uploads could otherwise both pass the check below, and the
        # unique constraint would then reject their whole batch.
        User.objects.select_for_update(no_key=True).get(pk=upload.merchant_id)
        numbers = [cleaned.get("invoice_number") for _, cleaned, _ in rows]
        taken = set(
            Invoice.objects.filter(
                merchant_id=upload.merchant_id, invoice_number__in=numbers
            ).values_list("invoice_number", flat=True)
        )

        invoices, row_errors = [], []
        for number, cleaned, errors in rows:
            if cleaned.get("invoice_number") in taken:
                errors["invoice_number"] = [DUPLICATE_MESSAGE]
            if errors:
                row_errors.append(
                    UploadRowError(upload=upload, row_number=number, errors=errors)
                )
                continue
            taken.add(cleaned["invoice_number"])
            invoices.append(
                Invoice(
                    merchant_id=upload.merchant_id,
                    upload=upload,
                    row_number=number,
                    **cleaned,
                )
            )

        Invoice.objects.bulk_create(invoices)
        UploadRowError.objects.bulk_create(row_errors)
        _update(
            upload.pk,
            processed_rows=processed + len(rows),
            successful_rows=current.successful_rows + len(invoices),
            failed_rows=current.failed_rows + len(row_errors),
        )
    return True


def _rows(upload):
    extension = Path(upload.file.name).suffix
    with upload.file.storage.open(upload.file.name, "rb") as file:
        yield from iter_rows(file, extension)


def _update(upload_id, **fields):
    Upload.objects.filter(pk=upload_id).update(updated_at=timezone.now(), **fields)


def _fail(upload_id, reason):
    _update(upload_id, status=Upload.Status.FAILED, failure_reason=reason)
