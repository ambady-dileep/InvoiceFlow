import codecs
import csv
import io
import zipfile
from itertools import islice
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError

REQUIRED_COLUMNS = (
    "invoice_number",
    "customer_name",
    "amount",
    "invoice_date",
    "status",
)

# Content types are client-supplied, so the content checks below are the real test.
CONTENT_TYPES = {
    ".csv": {
        "text/csv",
        "text/plain",
        "application/csv",
        "application/vnd.ms-excel",
        "application/octet-stream",
    },
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    },
}

CSV_SAMPLE_SIZE = 64 * 1024


def normalize_headers(headers):
    return ["" if header is None else str(header).strip().lower() for header in headers]


def missing_columns(headers):
    return [column for column in REQUIRED_COLUMNS if column not in headers]


def validate_upload_file(file):
    extension = Path(file.name).suffix.lower()
    if extension not in CONTENT_TYPES:
        raise ValidationError("Only CSV and XLSX files are supported.")
    if file.size > settings.MAX_UPLOAD_SIZE:
        limit = settings.MAX_UPLOAD_SIZE // (1024 * 1024)
        raise ValidationError(f"The file must not be larger than {limit} MB.")
    if file.content_type not in CONTENT_TYPES[extension]:
        raise ValidationError("The file type does not match its extension.")

    if extension == ".csv":
        _validate_csv(file)
    else:
        _validate_xlsx(file)


def _validate_csv(file):
    """Check the encoding and headers from a sample, without reading every row."""
    file.seek(0)
    sample = file.read(CSV_SAMPLE_SIZE)
    file.seek(0)

    # The sample may end partway through a character, which this decoder allows.
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    try:
        text = decoder.decode(sample)
    except UnicodeDecodeError:
        raise ValidationError("CSV files must be UTF-8 encoded.")
    if "\x00" in text:
        raise ValidationError("The file is not a valid CSV file.")

    try:
        rows = list(islice(filter(None, csv.reader(io.StringIO(text))), 2))
    except csv.Error:
        raise ValidationError("The file is not a valid CSV file.")
    if not rows:
        raise ValidationError("The file has no header row.")

    missing = missing_columns(normalize_headers(rows[0]))
    if missing:
        raise ValidationError(f"Missing required columns: {', '.join(missing)}.")
    if len(rows) < 2:
        raise ValidationError("The file has no invoice rows.")


def _validate_xlsx(file):
    """Check the archive structure only.

    Reading XLSX headers means parsing the shared strings table, which took 2.5s
    for 100,000 rows, so the Celery task checks headers instead.
    """
    file.seek(0)
    try:
        with zipfile.ZipFile(file) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        raise ValidationError("The file is not a valid XLSX file.")
    finally:
        file.seek(0)

    if not {"[Content_Types].xml", "xl/workbook.xml"} <= names:
        raise ValidationError("The file is not a valid XLSX file.")
