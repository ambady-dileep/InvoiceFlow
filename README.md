# InvoiceFlow

InvoiceFlow is in early setup. The application code has not been added yet.

## Structure

```text
invoiceflow/
├── backend/             Django backend
├── frontend/            Next.js frontend (not yet scaffolded)
├── docker-compose.yml   PostgreSQL, Redis, backend, and worker services
├── .env.example         Template for local configuration
├── .gitignore           Files kept out of Git
└── README.md
```

## Prerequisites

- Python 3
- Node.js and npm
- Git
- Docker Desktop

## Local services

Copy `.env.example` to `.env` and replace the placeholders, then run `docker compose up -d`.

| Service | Host address |
|---|---|
| PostgreSQL | `localhost:5433` |
| Redis | `localhost:6380` |
| Backend | `localhost:8000` |

Apply migrations with `docker compose exec backend python manage.py migrate`.
Check the backend at <http://localhost:8000/api/health/>.

Run the backend tests with `docker compose exec backend python manage.py test`.

## Authentication

| Endpoint | Body | Returns |
|---|---|---|
| `POST /api/auth/register/` | `username`, `email`, `password` | The new merchant |
| `POST /api/auth/login/` | `username`, `password` | `access` and `refresh` tokens |
| `POST /api/auth/token/refresh/` | `refresh` | A new `access` token |

All other API endpoints except `GET /api/health/` require an `Authorization: Bearer <access token>` header.

## Uploads

`POST /api/uploads/` takes a multipart `file` field containing a CSV or XLSX file. It returns `202 Accepted` with `upload_id`, `status`, `original_filename`, and `created_at`. Invalid files return `400` with the reason under `file`.

Required columns are `invoice_number`, `customer_name`, `amount`, `invoice_date`, and `status`. Other columns are allowed.

After the upload commits, a Celery worker reads the file in the background. Restart the worker after changing backend code, because it does not reload automatically.

Rows are saved in batches of 1,000. Valid rows become invoices. Invalid rows are stored in `UploadRowError` with their row number and an `errors` object that maps column names to lists of messages. Each batch's invoices, row errors, and counters commit together, so a retry resumes after the last committed batch without creating duplicates.

| Upload field | Meaning |
|---|---|
| `completed` status | The whole file was processed, even if some or all rows were invalid |
| `failed` status | The file itself could not be processed; `failure_reason` explains why |
| `total_rows` | Non-blank data rows in the file |
| `processed_rows` | Data rows handled so far, valid or invalid; always `successful_rows + failed_rows` |
| `successful_rows` | Rows stored as invoices |
| `failed_rows` | Rows stored as row errors |

An upload that fails partway keeps the batches it already committed.

`GET /api/uploads/` lists the signed-in merchant's uploads, newest first. It uses the same `page` and `page_size` parameters as the invoice list, and each result has the status fields below plus `original_filename`.

`GET /api/uploads/{id}/status/` returns `upload_id`, `status`, `progress` (a whole percentage that reaches 100 only when the upload is `completed`), `total_rows`, `processed_rows`, `successful_rows`, `failed_rows`, `failure_reason`, and `created_at`. `failure_reason` is empty unless the upload failed. Clients poll this endpoint for progress, which keeps the setup simple and needs no open connections.

`GET /api/uploads/{id}/errors/` downloads a CSV with the columns `row_number`, `column`, and `message`, and one line per message. An upload with no row errors returns only the header. The response is streamed, so large reports use little memory. Messages never repeat uploaded cell values, and any cell that could start a spreadsheet formula gets a leading `'`, so the report is safe to open in Excel with any delimiter.

Both endpoints return `404` for another merchant's upload, the same as for one that does not exist.

## Invoices

`GET /api/invoices/` lists the signed-in merchant's invoices, newest `invoice_date` first.

| Parameter | Purpose |
|---|---|
| `search` | Case-insensitive text within `invoice_number` or `customer_name`. Every term must match. |
| `status` | `pending`, `paid`, or `overdue` |
| `invoice_date__gte`, `invoice_date__lte` | Date range, including both ends |
| `amount__gte`, `amount__lte` | Amount range, including both ends |
| `ordering` | `invoice_number`, `customer_name`, `amount`, `invoice_date`, or `status`. Prefix with `-` to reverse. |
| `page`, `page_size` | `page_size` defaults to 25, up to 100 |

The response contains `count`, `next`, `previous`, and `results`. Each result has `id`, `invoice_number`, `customer_name`, `amount`, `invoice_date`, and `status`.

Invalid `status`, date, or amount values return `400`. A page that does not exist returns `404`.

## Assumptions

The assignment does not define an invoice schema, so these are interpretations:

- Invoice columns are `invoice_number`, `customer_name`, `amount`, `invoice_date`, and `status`, which cover the required search, filters, and sorting.
- All amounts use one currency and cannot be negative.
- Invoice status is `pending`, `paid`, or `overdue`.
- Invoice numbers are unique per merchant, within a file and across previous uploads. The first valid row keeps the number, later rows get an explicit row error, and re-uploading a file reports every row as a duplicate. Numbers are compared exactly, including case.
- Row rules: `invoice_number` is required, at most 50 characters; `customer_name` is required, at most 255 characters; `amount` is a finite decimal of zero or more with at most 2 decimal places and 14 digits; `invoice_date` is a `YYYY-MM-DD` date or a real Excel date; `status` is `pending`, `paid`, or `overdue`, in any case. Text is trimmed.
- The upload size limit is 25 MB, which allows about 250 bytes per row at 100,000 rows.
- CSV files must be UTF-8 encoded.
- CSV headers are checked during the request. XLSX headers and row counts of both formats need the whole file or its shared strings table, which took 2.5s for 100,000 rows, so the background task checks them.
- 100,000 rows is a maximum. Smaller files are accepted, and blank rows are not counted.
- XLSX files may decompress to at most 100 MB. A 100,000-row test file used about 29 MB.
- Invoice search covers `invoice_number` and `customer_name`, and the date and amount filters are ranges, because the assignment does not define them.
- An unknown `ordering` value uses the default order, and a range whose minimum is above its maximum returns no results.

## Known limitations

- After a worker is killed, its upload stays `processing` for up to 15 minutes until Redis redelivers the task.
- If the hard time limit fires, or the database stays down through every retry, an upload can stay `processing`. A periodic sweep of stale uploads would fix this, but it was left out to keep the scope small.
- A file that repeatedly kills the worker would be redelivered each time. The size limits make this unlikely.
- Invoice search scans the merchant's rows, because it has no text index. It took about 450 ms for each query at 500,000 invoices. A `pg_trgm` index would speed this up, at the cost of slower bulk inserts.
- Deep pages get slower with offset pagination, at about 200 ms at row 400,000.
- Pagination uses offsets, so an invoice added while a merchant is browsing can shift rows between pages and show a row twice.

Setup and run instructions for the frontend will be added once it exists.
