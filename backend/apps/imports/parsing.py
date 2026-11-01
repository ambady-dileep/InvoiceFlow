import csv
import io
import zipfile
import zlib
from xml.etree.ElementTree import ParseError

from django.conf import settings
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from apps.imports.validators import missing_columns, normalize_headers

XLSX_ERRORS = (
    zipfile.BadZipFile,
    zlib.error,
    InvalidFileException,
    ParseError,
    AttributeError,
    EOFError,
    IndexError,
    KeyError,
    NotImplementedError,
    RuntimeError,
    TypeError,
    ValueError,
)


class UploadFileError(Exception):
    """A permanent problem with the file. The message is safe to show to merchants."""


def iter_rows(file, extension):
    """Yield (row number, values by column) one row at a time, skipping blank rows."""
    rows = _read_xlsx(file) if extension == ".xlsx" else _read_csv(file)
    headers = None
    for number, values in enumerate(rows, start=1):
        if all(value is None or not str(value).strip() for value in values):
            continue
        if headers is not None:
            yield number, dict(zip(headers, values))
            continue

        headers = normalize_headers(values)
        missing = missing_columns(headers)
        if missing:
            raise UploadFileError(f"Missing required columns: {', '.join(missing)}.")

    if headers is None:
        raise UploadFileError("The file has no header row.")


def _read_csv(file):
    text = io.TextIOWrapper(file, encoding="utf-8-sig", newline="")
    try:
        yield from csv.reader(text)
    except UnicodeDecodeError:
        raise UploadFileError("CSV files must be UTF-8 encoded.")
    except csv.Error:
        raise UploadFileError("The file is not a valid CSV file.")


def _read_xlsx(file):
    try:
        with zipfile.ZipFile(file) as archive:
            size = sum(entry.file_size for entry in archive.infolist())
    except zipfile.BadZipFile:
        raise UploadFileError("The file is not a valid XLSX file.")
    # zipfile stops reading an entry at its declared size, so this can be trusted.
    if size > settings.MAX_XLSX_UNCOMPRESSED_SIZE:
        raise UploadFileError("The XLSX file is too large when decompressed.")

    file.seek(0)
    workbook = None
    try:
        workbook = load_workbook(file, read_only=True, data_only=True)
        yield from workbook.worksheets[0].iter_rows(values_only=True)
    except XLSX_ERRORS:
        raise UploadFileError("The file is not a valid XLSX file.")
    finally:
        if workbook is not None:
            workbook.close()
