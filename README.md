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

Setup and run instructions for the frontend will be added once it exists.
