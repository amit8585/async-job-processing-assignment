# Async Job Processing Service

A production-ready asynchronous job processing backend built with **FastAPI**, **Celery**, **PostgreSQL**, and **Redis**. Includes JWT authentication, rate limiting, pagination, job expiration, and a single-page HTML frontend.

---

## Architecture

```
┌─────────────┐      HTTP      ┌─────────────────┐
│   Browser   │ ─────────────► │   FastAPI (api) │
│  (frontend) │ ◄───────────── │   :8000         │
└─────────────┘                └────────┬────────┘
                                        │ enqueue task
                                        ▼
                               ┌─────────────────┐
                               │  Redis (broker) │
                               │  :6379          │
                               └────────┬────────┘
                                        │ consume task
                                        ▼
                               ┌─────────────────┐
                               │  Celery Worker  │
                               └────────┬────────┘
                                        │ read/write
                                        ▼
                               ┌─────────────────┐
                               │  PostgreSQL (db) │
                               │  :5432          │
                               └─────────────────┘
```

---

## Quick Start (Docker Compose)

```bash
# Clone / enter the project
cd Assignment_Amit

# Build and start all services
docker-compose up --build

# Access the API
open http://localhost:8000        # Frontend
open http://localhost:8000/docs   # Swagger UI
open http://localhost:8000/redoc  # ReDoc
```

### Services Started

| Service         | Port  | Description                        |
|-----------------|-------|------------------------------------|
| `api`           | 8000  | FastAPI application + frontend     |
| `celery_worker` | —     | Async job processor                |
| `celery_beat`   | —     | Periodic cleanup scheduler         |
| `db`            | 5432  | PostgreSQL 16                      |
| `redis`         | 6379  | Redis 7 (broker + result backend)  |

---

## API Reference

### Authentication

| Method | Endpoint          | Description              |
|--------|-------------------|--------------------------|
| POST   | `/auth/register`  | Register a new user      |
| POST   | `/auth/login`     | Login, get JWT token     |
| GET    | `/users/me`       | Get current user info    |

**Register:**
```json
POST /auth/register
{
  "username": "alice",
  "email": "alice@example.com",
  "password": "mysecretpassword"
}
```

**Login** (form-encoded):
```
POST /auth/login
username=alice&password=mysecretpassword
```
Returns: `{ "access_token": "...", "token_type": "bearer" }`

---

### Jobs

All job endpoints require `Authorization: Bearer <token>` header.

| Method | Endpoint                  | Description                             |
|--------|---------------------------|-----------------------------------------|
| POST   | `/jobs/`                  | Submit a new job                        |
| GET    | `/jobs/`                  | List jobs (paginated + filtered)        |
| GET    | `/jobs/{job_id}/status`   | Get job status                          |
| GET    | `/jobs/{job_id}/result`   | Get job result                          |
| DELETE | `/jobs/{job_id}`          | Delete a job                            |

**Submit a job:**
```json
POST /jobs/
{
  "data": [1, 2, 3, 4, 5],
  "operation": "square_sum"
}
```
Supported operations: `square_sum`, `cube_sum`

Response:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PENDING",
  "message": "Job submitted successfully"
}
```

**Job Status values:** `PENDING` → `IN_PROGRESS` → `SUCCESS` | `FAILED`

**List jobs with filters:**
```
GET /jobs/?page=1&page_size=10&status=SUCCESS&operation=square_sum
```

---

## Operations

| Operation    | Formula              | Example input `[1,2,3]`  |
|--------------|----------------------|--------------------------|
| `square_sum` | Σ(xᵢ²)              | 1 + 4 + 9 = **14**       |
| `cube_sum`   | Σ(xᵢ³)              | 1 + 8 + 27 = **36**      |

---

## Bonus Features

- **JWT Authentication** — Tokens expire after 30 minutes
- **User-scoped jobs** — Users can only see/delete their own jobs
- **Rate limiting** — 100 requests/minute per user (via `slowapi`)
- **Pagination** — `page` and `page_size` query params
- **Filtering** — Filter by `status` and `operation`
- **Job expiration** — Jobs expire after 24 hours (configurable via `JOB_EXPIRY_HOURS`)
- **Swagger UI** — Auto-generated at `/docs`
- **Frontend** — Single-page app at `/static/index.html`
- **Health checks** — `GET /health`

---

## Project Structure

```
.
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI app, lifespan, middleware
│   ├── models.py        # SQLAlchemy ORM models (User, Job)
│   ├── tasks.py         # Celery task definitions
│   ├── db.py            # Async DB engine + session
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py    # All API endpoints + JWT auth
│   │   └── schemas.py   # Pydantic request/response models
│   └── static/
│       └── index.html   # Single-page frontend
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   └── test_jobs.py     # Comprehensive test suite
├── Dockerfile
├── docker-compose.yml
├── pytest.ini
├── requirements.txt
└── README.md
```

---

## Running Tests

Tests use **SQLite in-memory** via `aiosqlite` — no external services needed.

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
pytest

# Run with coverage
pip install pytest-cov
pytest --cov=app --cov-report=term-missing
```

### Test Coverage Areas

- User registration (success, duplicate, validation)
- User login (success, wrong password, nonexistent user)
- Job submission (authenticated, unauthenticated, invalid operation, empty data)
- Job status (own job, other user's job, not found)
- Job result (pending, in-progress, success, failed)
- Job listing (pagination, filtering by status/operation, user isolation)
- Job deletion (owner, non-owner, nonexistent)
- Celery task computation logic (square_sum, cube_sum, edge cases)
- Health endpoints
- Edge cases (invalid UUID, expired token, data size limits)

---

## Configuration

| Environment Variable   | Default                                              | Description             |
|------------------------|------------------------------------------------------|-------------------------|
| `DATABASE_URL`         | `postgresql+asyncpg://postgres:postgres@db:5432/jobsdb` | Async DB URL         |
| `DATABASE_URL_SYNC`    | `postgresql+psycopg2://postgres:postgres@db:5432/jobsdb` | Sync DB URL (Celery) |
| `REDIS_URL`            | `redis://redis:6379/0`                               | Redis broker URL        |
| `SECRET_KEY`           | `supersecretkey-change-in-production-please`         | JWT signing key         |
| `JOB_EXPIRY_HOURS`     | `24`                                                 | Job TTL in hours        |

---

## Development (without Docker)

```bash
# Start infrastructure
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=jobsdb postgres:16-alpine
docker run -d -p 6379:6379 redis:7-alpine

# Install dependencies
pip install -r requirements.txt

# Set env vars
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/jobsdb"
export DATABASE_URL_SYNC="postgresql+psycopg2://postgres:postgres@localhost:5432/jobsdb"
export REDIS_URL="redis://localhost:6379/0"

# Run API
uvicorn app.main:app --reload --port 8000

# Run Celery worker (separate terminal)
celery -A app.tasks.celery_app worker --loglevel=info
```
