# InvoiceFlow

InvoiceFlow is in early setup. The application code has not been added yet.

## Structure

```text
invoiceflow/
├── backend/             Django backend
├── frontend/            Next.js frontend
├── docker-compose.yml   PostgreSQL, Redis, backend, worker, and frontend services
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

Copy `.env.example` to `.env` and replace the placeholders, then run `docker compose up -d --build`. The backend applies migrations when it starts.

| Service | Host address |
|---|---|
| PostgreSQL | `localhost:5433` |
| Redis | `localhost:6380` |
| Backend | `localhost:8000` |
| Frontend | `localhost:3000` |

Open the app at <http://localhost:3000>. Use `localhost`, not `127.0.0.1`, because the API only allows that origin.
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

## Caching

Redis caches one thing: the invoice list's total `count` for each combination of filters. It is the slow query that repeats without changing, because every page turn and sort recounts the same rows. It took about 62 ms unfiltered and 450 ms for a search at 500,000 invoices. Invoice rows are never cached, so the list always shows current data.

- **Key:** `invoices:count:{merchant_id}:{version}:{hash of the filters}`. Page, page size, and ordering are left out, so they share one count. The merchant ID keeps merchants apart.
- **Invalidation:** each merchant has a random version token. After a batch of invoices commits, the worker deletes that merchant's token, so all their cached counts stop being read at once, and the next request creates a new token. Invalidating after the commit means a reader cannot cache the old total again under the new token.
- **Expiry:** counts expire after 5 minutes, which also limits staleness if an invalidation fails or invoices are removed some other way, such as by deleting a merchant.
- **Failure:** if Redis is unavailable, the list counts from the database instead of failing. While the Redis container is stopped, each list request takes about 4 seconds, because the failed DNS lookup runs inside the request. A refused connection fails immediately.

Caching rows was rejected, because search, filter, and page combinations would rarely repeat, and it would risk showing stale invoices. Dashboard summary counts were rejected, because they would need a new endpoint and screen.

## Exports

`POST /api/invoices/export/` takes the same search, filter, and `ordering` query parameters as the invoice list, ignores pagination, and returns `202 Accepted` with an `export_id`. A Celery task then writes the CSV in the background.

`GET /api/exports/{id}/` returns `export_id`, `status` (`pending`, `processing`, `completed`, or `failed`), `row_count`, `failure_reason`, `download_url`, and `created_at`. `download_url` is empty until the export is `completed`.

`GET /api/exports/{id}/download/` returns the CSV, with the columns `invoice_number`, `customer_name`, `amount`, `invoice_date`, and `status`, which match the upload format. The internal `id` is not included. It needs the same `Authorization` header as other endpoints, because files are never served publicly.

All export endpoints return `404` for another merchant's export. The download also returns `404` until the export is `completed`.

## Seed data

Generate a large test file to upload through the app:

```bash
docker compose exec backend python manage.py seed_invoices --rows 100000 --output invoices.csv
```

The file appears at `backend/invoices.csv`, which Git ignores. An existing file is never replaced.

Or add invoices straight to a merchant, skipping the upload flow:

```bash
docker compose exec backend python manage.py seed_invoices --rows 100000 --merchant your_username
```

`--rows` must be between 10,000 and 100,000, and defaults to 10,000. `--seed` defaults to 42, and the same seed gives the same data.

Database seeding always adds rows and never changes existing data. It creates one completed `Upload` for each run, and invoice numbers include that upload's ID, so running it again cannot collide. If it fails, nothing is kept. It does not queue Celery tasks, and it clears the merchant's cached count once at the end.

Every generated row is valid, so the file shows no row errors. Uploading the same file twice reports every row as a duplicate. Seeded uploads have no stored file.

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
- The cached total is cleared after each batch, so it stays current during an upload, but the cache gives little benefit while one is running.
- Pagination uses offsets, so an invoice added while a merchant is browsing can shift rows between pages and show a row twice.
- An export reflects invoices when the task reads them, not when the button was clicked. If an upload is still running, the file may include some of its rows.
- Export files are kept in storage indefinitely, because there is no cleanup job.
- The Invoices page tracks only the current export. Changing the search, filters, or sorting clears it, and so does reloading the page. An export started before that keeps running, but its file can no longer be reached from the page, because there is no export history.
- Deleting a merchant removes its upload and export records, but not their stored files.
- In exported text cells, a value that could start a spreadsheet formula gets a leading `'`, so those values differ from the original.
