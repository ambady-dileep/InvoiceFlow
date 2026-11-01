import logging
from pathlib import Path

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import InterfaceError, OperationalError
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerError

from apps.imports.models import Upload
from apps.imports.parsing import UploadFileError, iter_rows

logger = logging.getLogger(__name__)

PROGRESS_INTERVAL = 1000
RETRY_DELAY = 30
TRANSIENT_ERRORS = (OperationalError, InterfaceError, OSError)


def queue_upload(upload):
    """Queue processing. Runs after commit, so a broker failure must not be lost."""
    try:
        process_upload.delay(upload.id)
    except BrokerError:
        logger.exception("Could not queue upload %s", upload.id)
        upload.status = Upload.Status.FAILED
        upload.failure_reason = "The upload could not be queued for processing."
        upload.save(update_fields=["status", "failure_reason", "updated_at"])


@shared_task(bind=True, max_retries=3)
def process_upload(self, upload_id):
    try:
        upload = _claim(upload_id)
        if upload is None:
            return
        _update(upload_id, total_rows=_count_rows(upload))
        processed = _read_rows(upload)
        _update(upload_id, status=Upload.Status.COMPLETED, processed_rows=processed)
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
    """Start or restart an unfinished upload. Finished uploads are left alone."""
    claimed = Upload.objects.filter(
        pk=upload_id,
        status__in=[Upload.Status.PENDING, Upload.Status.PROCESSING],
    ).update(
        status=Upload.Status.PROCESSING,
        total_rows=0,
        processed_rows=0,
        successful_rows=0,
        failed_rows=0,
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


def _read_rows(upload):
    processed = 0
    for _ in _rows(upload):
        processed += 1
        if processed % PROGRESS_INTERVAL == 0:
            _update(upload.id, processed_rows=processed)
    return processed


def _rows(upload):
    extension = Path(upload.file.name).suffix
    with upload.file.storage.open(upload.file.name, "rb") as file:
        yield from iter_rows(file, extension)


def _update(upload_id, **fields):
    Upload.objects.filter(pk=upload_id).update(updated_at=timezone.now(), **fields)


def _fail(upload_id, reason):
    _update(upload_id, status=Upload.Status.FAILED, failure_reason=reason)
