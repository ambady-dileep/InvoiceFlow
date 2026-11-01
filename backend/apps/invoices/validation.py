from datetime import date

from django.core.exceptions import ValidationError

from apps.imports.validators import REQUIRED_COLUMNS
from apps.invoices.models import Invoice

# Django's versions of these repeat the cell value. Row errors end up in a CSV
# report, where a repeated value could become a spreadsheet formula.
MESSAGES = {
    ("amount", "invalid"): "Enter a valid number.",
    ("invoice_date", "invalid"): "Enter a valid date in YYYY-MM-DD format.",
    ("invoice_date", "invalid_date"): "Enter a valid date in YYYY-MM-DD format.",
    ("status", "invalid_choice"): "Enter one of: pending, paid, overdue.",
}


def validate_row(row):
    """Return (cleaned values, errors by column), checking every column."""
    cleaned, errors = {}, {}
    for column in REQUIRED_COLUMNS:
        value = _normalize(column, row.get(column))
        if value in (None, ""):
            errors[column] = ["This field is required."]
        elif isinstance(value, str) and "\x00" in value:
            errors[column] = ["This field contains an invalid character."]
        else:
            try:
                cleaned[column] = Invoice._meta.get_field(column).clean(value, None)
            except ValidationError as error:
                errors[column] = [
                    MESSAGES.get((column, item.code), item.messages[0])
                    for item in error.error_list
                ]
    return cleaned, errors


def _normalize(column, value):
    if value is None or isinstance(value, date):
        return value
    # Excel numbers become text, so an amount keeps its written precision.
    text = str(value).strip()
    return text.lower() if column == "status" else text
