# InvoiceFlow

InvoiceFlow is in early setup. The application code has not been added yet.

## Structure

```text
invoiceflow/
├── backend/             Django backend
├── frontend/            Next.js frontend (not yet scaffolded)
├── docker-compose.yml   PostgreSQL, Redis, and backend services
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

## Assumptions

The assignment does not define an invoice schema, so these are interpretations:

- Invoice columns are `invoice_number`, `customer_name`, `amount`, `invoice_date`, and `status`, which cover the required search, filters, and sorting.
- All amounts use one currency and cannot be negative.
- Invoice status is `pending`, `paid`, or `overdue`.
- Invoice numbers are unique per merchant, including across uploads.
- The upload size limit is 25 MB, which allows about 250 bytes per row at 100,000 rows.
- CSV files must be UTF-8 encoded.
- CSV headers are checked during the request. XLSX headers and row counts of both formats need the whole file or its shared strings table, which took 2.5s for 100,000 rows, so the background task checks them.

Setup and run instructions for the frontend will be added once it exists.
