from datetime import date

from django.core.exceptions import ValidationError

from apps.imports.validators import REQUIRED_COLUMNS
from apps.invoices.models import Invoice


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
                errors[column] = error.messages
    return cleaned, errors


def _normalize(column, value):
    if value is None or isinstance(value, date):
        return value
    # Excel numbers become text, so an amount keeps its written precision.
    text = str(value).strip()
    return text.lower() if column == "status" else text
