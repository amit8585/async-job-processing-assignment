# Architecture & Implementation Document

## Async Job Processing Service

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Project Structure](#3-project-structure)
4. [Technology Stack](#4-technology-stack)
5. [Component Breakdown](#5-component-breakdown)
6. [Data Models](#6-data-models)
7. [API Reference](#7-api-reference)
8. [Authentication & Security](#8-authentication--security)
9. [Job Processing Pipeline](#9-job-processing-pipeline)
10. [Frontend Architecture](#10-frontend-architecture)
11. [Testing Strategy](#11-testing-strategy)
12. [Infrastructure & Deployment](#12-infrastructure--deployment)
13. [Configuration & Environment](#13-configuration--environment)

---

## 1. Project Overview

This service provides a production-ready **asynchronous job processing platform** where users submit computational tasks (jobs), which are processed in the background by distributed workers. Users can track job status in real-time via a web UI or REST API.

**Core Capabilities:**
- User registration and JWT-based authentication
- Asynchronous job submission and processing (square sum, cube sum of number lists)
- Real-time job status tracking with polling
- Per-user rate limiting and data isolation
- Automatic expiration and cleanup of old jobs
- Fully containerized, multi-service deployment

---

## 2. System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Client Layer                            │
│                  Browser (SPA) / API Client                     │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP (port 8000)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI Application                        │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │  Auth Routes │  │  Job Routes  │  │  Static Files (SPA)   │ │
│  │  /auth/*     │  │  /jobs/*     │  │  /static/             │ │
│  └──────────────┘  └──────┬───────┘  └───────────────────────┘ │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Middleware: CORS, Rate Limiting (100 req/min per user)  │   │
│  └──────────────────────────────────────────────────────────┘   │
└───────────┬────────────────────────┬────────────────────────────┘
            │ async SQLAlchemy       │ Celery enqueue
            ▼                       ▼
┌───────────────────┐   ┌───────────────────────────────────────┐
│   PostgreSQL 16   │   │              Redis 7                  │
│   (Port 5432)     │   │         (Port 6379/6380)              │
│                   │   │   Message Broker + Results Backend    │
│  ┌─────────────┐  │   └──────────────────┬────────────────────┘
│  │    users    │  │                      │ consume
│  │    jobs     │  │                      ▼
│  └─────────────┘  │   ┌───────────────────────────────────────┐
└───────────────────┘   │           Celery Workers              │
         ▲              │   ┌───────────────┐ ┌───────────────┐ │
         │  write back  │   │  Worker Pool  │ │  Beat Sched.  │ │
         └──────────────│   │ (4 concurrent)│ │ (hourly cron) │ │
                        │   └───────────────┘ └───────────────┘ │
                        └───────────────────────────────────────┘
```

### Request Flow

```
User Request
    │
    ├─► JWT Validation (every protected route)
    │
    ├─► Rate Limit Check (100 req/min per user ID)
    │
    ├─► Route Handler (async)
    │       │
    │       ├─► DB Query (async SQLAlchemy + asyncpg)
    │       │
    │       └─► Celery Enqueue (job submission only)
    │
    └─► JSON Response
```

### Job Processing Flow

```
POST /jobs/
    │
    ├─► Validate request (Pydantic)
    ├─► Create Job record (status=PENDING, expires_at=+24h)
    ├─► Enqueue to Redis → process_job.delay(job_id)
    └─► Return 201 with job_id

Redis Queue
    │
    └─► Celery Worker picks up task
            │
            ├─► Update status → IN_PROGRESS
            ├─► Sleep 2s (simulated work)
            ├─► Compute result (square_sum or cube_sum)
            ├─► Update status → SUCCESS, write result
            └─► On error: retry up to 3 times → FAILED
```

---

## 3. Project Structure

```
Assignment_Amit/
│
├── app/                          # Application source code
│   ├── __init__.py
│   ├── main.py                   # FastAPI app factory, lifespan, middleware
│   ├── models.py                 # SQLAlchemy ORM models (User, Job)
│   ├── db.py                     # Async DB engine, session factory
│   ├── tasks.py                  # Celery app and task definitions
│   │
│   ├── api/                      # API layer
│   │   ├── __init__.py
│   │   ├── routes.py             # All endpoint handlers, auth logic
│   │   └── schemas.py            # Pydantic request/response schemas
│   │
│   └── static/
│       └── index.html            # Single-page application (SPA)
│
├── tests/                        # Test suite
│   ├── __init__.py
│   ├── conftest.py               # Fixtures, test DB, mock Celery
│   └── test_jobs.py              # Full API test coverage
│
├── Dockerfile                    # Multi-stage Docker build
├── docker-compose.yml            # 5-service orchestration
├── requirements.txt              # Python dependencies
├── pytest.ini                    # Test configuration
└── README.md                     # Quick-start documentation
```

---

## 4. Technology Stack

| Layer | Technology | Version | Purpose |
|---|---|---|---|
| Web Framework | FastAPI | 0.115.0 | Async HTTP API |
| ASGI Server | Uvicorn | 0.30.6 | Production server |
| Database | PostgreSQL | 16 | Persistent storage |
| ORM | SQLAlchemy | 2.0.36 | Async ORM |
| DB Driver | asyncpg | 0.29.0 | Async Postgres driver |
| Task Queue | Celery | 5.4.0 | Distributed task execution |
| Message Broker | Redis | 7 | Queue + results backend |
| Authentication | python-jose | 3.3.0 | JWT creation/validation |
| Password Hashing | passlib (bcrypt) | 1.7.4 | Secure password storage |
| Rate Limiting | slowapi | 0.1.9 | Per-user request throttling |
| Data Validation | Pydantic | v2 | Schema validation |
| Frontend | Vanilla JS | — | Browser SPA (no build step) |
| Containerization | Docker + Compose | — | Multi-service deployment |
| Testing | pytest + asyncio | 8.3.3 | Async-native test suite |
| Test HTTP Client | httpx | 0.27.2 | Async HTTP test client |
| Test DB | aiosqlite | 0.20.0 | In-memory SQLite for tests |
| Migrations | Alembic | 1.14.0 | Schema migrations (installed) |
| Python | CPython | 3.12 | Runtime |

---

## 5. Component Breakdown

### 5.1 `app/main.py` — Application Entry Point

**Responsibilities:**
- Creates the FastAPI application instance
- Configures lifespan (startup/shutdown hooks): creates DB tables on start, cleans expired jobs on stop
- Attaches SlowAPI rate limiting middleware
- Implements custom rate key function: extracts user ID from JWT for per-user limits (falls back to IP for unauthenticated)
- Configures CORS (all origins allowed)
- Mounts static files at `/static/`
- Registers health endpoints (`GET /`, `GET /health`)
- Includes the API router

**Rate Limiting Design:**
```
Key = user_id (from JWT claim) if authenticated
    = client IP address if unauthenticated
Limit = 100 requests / minute
```

---

### 5.2 `app/models.py` — ORM Models

**User Model:**
```
users
├── id           UUID (PK, default=uuid4)
├── username     String(50), UNIQUE, NOT NULL, indexed
├── email        String(255), UNIQUE, NOT NULL, indexed
├── hashed_password  String, NOT NULL
└── created_at   DateTime (UTC, server default)
```

**Job Model:**
```
jobs
├── id           UUID (PK, default=uuid4)
├── user_id      UUID (FK → users.id, NOT NULL, indexed)
├── data         JSON (list of numbers)
├── operation    String ("square_sum" | "cube_sum")
├── status       String ("PENDING" | "IN_PROGRESS" | "SUCCESS" | "FAILED")
├── result       JSON (nullable, written on completion)
├── created_at   DateTime (UTC)
├── updated_at   DateTime (UTC, auto-updated)
└── expires_at   DateTime (UTC, set to created_at + 24h)
```

---

### 5.3 `app/db.py` — Database Layer

- Creates async engine: `postgresql+asyncpg://postgres:postgres@db:5432/jobsdb`
- `AsyncSessionLocal`: session factory with `expire_on_commit=False`
- `get_db()`: FastAPI dependency that yields an async session and commits/rolls back automatically
- `Base`: SQLAlchemy `DeclarativeBase` shared by all models

---

### 5.4 `app/tasks.py` — Celery Tasks

**Celery Configuration:**
```python
broker_url      = "redis://redis:6379/0"
result_backend  = "redis://redis:6379/0"
task_serializer = "json"
task_track_started = True
task_acks_late  = True      # Ack only after successful processing
worker_prefetch_multiplier = 1  # Prevent task hoarding
```

**`process_job(job_id)` Task:**
- Retries: max 3, countdown 5 seconds between retries
- Flow: fetch job → set IN_PROGRESS → sleep 2s → compute → set SUCCESS
- Error path: set FAILED after all retries exhausted

**`cleanup_expired_jobs()` Task (Beat Schedule):**
- Runs every hour via Celery Beat
- Deletes all `Job` records where `expires_at < datetime.utcnow()`
- Returns count of deleted records

**Operations:**
| Operation | Formula | Example (data=[1,2,3]) |
|---|---|---|
| `square_sum` | sum(x² for x in data) | 1 + 4 + 9 = 14 |
| `cube_sum` | sum(x³ for x in data) | 1 + 8 + 27 = 36 |

---

### 5.5 `app/api/routes.py` — API Handlers

**Auth Configuration:**
```
SECRET_KEY     = env SECRET_KEY (default: "supersecretkey-change-in-production-please")
ALGORITHM      = HS256
TOKEN_EXPIRY   = 30 minutes
```

**Endpoint Summary:**

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/auth/register` | No | Create new user account |
| POST | `/auth/login` | No | Login, receive JWT |
| GET | `/users/me` | Yes | Get current user info |
| POST | `/jobs/` | Yes | Submit new job |
| GET | `/jobs/` | Yes | List jobs (paginated, filtered) |
| GET | `/jobs/{id}/status` | Yes | Poll job status |
| GET | `/jobs/{id}/result` | Yes | Get job result |
| DELETE | `/jobs/{id}` | Yes | Delete a job |

---

### 5.6 `app/api/schemas.py` — Pydantic Schemas

| Schema | Direction | Key Fields |
|---|---|---|
| `UserRegisterRequest` | Request | username, email, password |
| `UserRegisterResponse` | Response | id, username, email, created_at |
| `TokenResponse` | Response | access_token, token_type |
| `JobSubmitRequest` | Request | data (1–1000 numbers), operation |
| `JobSubmitResponse` | Response | job_id, status, expires_at |
| `JobStatusResponse` | Response | job_id, status, operation, created_at, updated_at |
| `JobResultResponse` | Response | job_id, status, result, message |
| `PaginatedJobsResponse` | Response | items[], total, page, page_size, total_pages |

**Job submission validation:**
- `data`: list of numbers, minimum 1 item, maximum 1000 items
- `operation`: must match pattern `square_sum` or `cube_sum`

---

## 6. Data Models

### Entity Relationship

```
users 1 ──────────── * jobs
  │                       │
  ├── id (PK)             ├── id (PK)
  ├── username            ├── user_id (FK)
  ├── email               ├── data (JSON)
  ├── hashed_password     ├── operation
  └── created_at          ├── status
                          ├── result (JSON)
                          ├── created_at
                          ├── updated_at
                          └── expires_at
```

### Job Status State Machine

```
                ┌──────────┐
     submit     │          │
────────────►   │ PENDING  │
                │          │
                └────┬─────┘
                     │ worker picks up
                     ▼
                ┌──────────────┐
                │              │
                │  IN_PROGRESS │
                │              │
                └──────┬───────┘
                       │
            ┌──────────┴──────────┐
            │ success             │ error (after 3 retries)
            ▼                     ▼
       ┌─────────┐          ┌────────┐
       │ SUCCESS │          │ FAILED │
       └─────────┘          └────────┘
```

---

## 7. API Reference

### Authentication

**Register**
```
POST /auth/register
Content-Type: application/json

{
  "username": "alice",
  "email": "alice@example.com",
  "password": "secret123"
}
```

**Login**
```
POST /auth/login
Content-Type: application/x-www-form-urlencoded

username=alice&password=secret123

Response:
{ "access_token": "<jwt>", "token_type": "bearer" }
```

### Jobs

**Submit Job**
```
POST /jobs/
Authorization: Bearer <token>
Content-Type: application/json

{
  "data": [1, 2, 3, 4, 5],
  "operation": "square_sum"
}

Response 201:
{ "job_id": "<uuid>", "status": "PENDING", "expires_at": "..." }
```

**List Jobs**
```
GET /jobs/?page=1&page_size=20&status=SUCCESS&operation=square_sum
Authorization: Bearer <token>

Response:
{
  "items": [...],
  "total": 42,
  "page": 1,
  "page_size": 20,
  "total_pages": 3
}
```

**Get Result**
```
GET /jobs/{job_id}/result
Authorization: Bearer <token>

Response:
{
  "job_id": "<uuid>",
  "status": "SUCCESS",
  "result": 55,
  "message": "Job completed successfully."
}
```

---

## 8. Authentication & Security

### JWT Authentication

1. User logs in with `username` + `password` (form-encoded)
2. Password verified with bcrypt
3. JWT issued with payload: `{ "sub": "<user_id>", "exp": <30min> }`
4. All protected routes validate Bearer token in `Authorization` header
5. User object resolved from `sub` claim for every request

### Security Properties

| Property | Implementation |
|---|---|
| Password storage | bcrypt hashing (salted) via passlib |
| Token signing | HMAC-SHA256 (HS256) |
| Token expiry | 30 minutes |
| User isolation | All DB queries filter by `user_id` from token |
| Rate limiting | 100 req/min per authenticated user ID |
| Secret key | Configurable via `SECRET_KEY` env variable |
| Non-root container | Runs as `appuser` (UID 1000) in Docker |

### User Isolation

Every job query includes an explicit ownership check:
```python
WHERE jobs.user_id = current_user.id
```
Users cannot access, view, or delete other users' jobs.

---

## 9. Job Processing Pipeline

### Submission

```python
# 1. Validate input
job_data = JobSubmitRequest(data=[1,2,3], operation="square_sum")

# 2. Persist to DB (status=PENDING)
job = Job(user_id=user.id, data=job_data.data,
          operation=job_data.operation, status="PENDING",
          expires_at=utcnow() + 24h)

# 3. Enqueue (fire and forget)
process_job.delay(str(job.id))

# 4. Return immediately
return {"job_id": job.id, "status": "PENDING"}
```

### Worker Execution

```python
@celery.task(bind=True, max_retries=3, default_retry_delay=5)
def process_job(self, job_id):
    job.status = "IN_PROGRESS"
    db.commit()

    time.sleep(2)  # Simulated processing delay

    if job.operation == "square_sum":
        result = sum(x**2 for x in job.data)
    elif job.operation == "cube_sum":
        result = sum(x**3 for x in job.data)

    job.status = "SUCCESS"
    job.result = result
    db.commit()
```

### Polling (Frontend)

```
Every 2 seconds:
  GET /jobs/{id}/status
    → if status in {SUCCESS, FAILED}: stop polling, show result
    → else: continue polling
```

---

## 10. Frontend Architecture

The frontend is a **zero-build SPA** (`app/static/index.html`) served directly from the FastAPI app. It uses vanilla JavaScript with Tailwind-inspired inline CSS.

### State Management

```
localStorage:
  auth_token  → JWT Bearer token
  username    → Display name

Page Sections (toggled by auth state):
  #auth-section   → Login / Register forms
  #app-section    → Job submission + job list + monitor
```

### Component Structure

```
index.html
├── Auth Section
│   ├── Login Form (username + password)
│   └── Register Form (username + email + password)
│
└── App Section
    ├── Header (username + logout)
    ├── Job Submission Form
    │   ├── Data textarea (comma-separated numbers)
    │   └── Operation dropdown (square_sum, cube_sum)
    ├── Active Job Monitor
    │   └── Real-time polling (2s interval) for pending/in-progress jobs
    └── Job History Table
        ├── Filters: status, operation
        ├── Pagination controls
        └── Per-row actions: View Result, Delete
```

### API Client

All API calls use `fetch()` with:
- `Authorization: Bearer <token>` header on authenticated calls
- JSON body for job submission
- Form-encoded body for login

---

## 11. Testing Strategy

### Test Configuration (`conftest.py`)

| Aspect | Approach |
|---|---|
| Database | In-memory SQLite (`sqlite+aiosqlite:///./test_db.sqlite3`) |
| Celery | Mocked — `process_job.delay` replaced with no-op |
| Table lifecycle | Created once per session, data cleared between tests |
| HTTP client | `httpx.AsyncClient` with ASGITransport (no real HTTP) |
| Lifespan | Mocked to skip real DB engine initialization |

### Test Structure (`test_jobs.py`)

```
tests/
└── test_jobs.py
    ├── Helpers
    │   ├── register_user(client, ...)
    │   ├── login_user(client, ...)
    │   ├── register_and_login(client, ...)
    │   └── auth_headers(token)
    │
    ├── TestUserRegistration
    │   ├── test_register_success
    │   ├── test_duplicate_username
    │   ├── test_duplicate_email
    │   ├── test_short_username
    │   ├── test_weak_password
    │   └── test_invalid_email
    │
    ├── TestUserLogin
    │   ├── test_login_success
    │   ├── test_wrong_password
    │   ├── test_nonexistent_user
    │   └── test_get_current_user
    │
    └── TestJobSubmission
        ├── test_submit_square_sum
        ├── test_submit_cube_sum
        ├── test_unauthenticated_rejection
        ├── test_invalid_operation
        └── test_empty_data
```

### Running Tests

```bash
# With Docker
docker compose run --rm app pytest

# Locally
pytest tests/ -v
```

---

## 12. Infrastructure & Deployment

### Docker Services (`docker-compose.yml`)

| Service | Image | Port | Purpose |
|---|---|---|---|
| `db` | postgres:16 | 5432 | Primary database |
| `redis` | redis:7 | 6380:6379 | Message broker + cache |
| `app` | (built from Dockerfile) | 8000 | FastAPI + static files |
| `worker` | (same image) | — | Celery worker (4 concurrent) |
| `beat` | (same image) | — | Celery periodic task scheduler |

All services have health checks. `app`, `worker`, and `beat` wait for `db` and `redis` to be healthy before starting.

### Dockerfile (Multi-Stage Build)

**Stage 1 — Builder:**
```
python:3.12-slim
├── Install: gcc, libpq-dev (build deps)
├── Create virtualenv at /opt/venv
└── pip install -r requirements.txt
```

**Stage 2 — Runtime:**
```
python:3.12-slim
├── Install: libpq5 (runtime only, no compiler)
├── Copy /opt/venv from builder
├── Create non-root user: appuser (UID 1000)
├── Copy application code
└── EXPOSE 8000
    CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Benefits:**
- Smaller final image (no build tools in runtime)
- Non-root execution (security hardening)
- Layer caching on dependencies

### Quick Start

```bash
# Start all services
docker compose up --build

# Access points
# API:      http://localhost:8000
# Docs:     http://localhost:8000/docs
# Frontend: http://localhost:8000/static/index.html

# Run tests
docker compose run --rm app pytest -v
```

---

## 13. Configuration & Environment

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@db:5432/jobsdb` | Postgres connection string |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection string |
| `SECRET_KEY` | `supersecretkey-change-in-production-please` | JWT signing key |

**Production Checklist:**
- Set `SECRET_KEY` to a cryptographically random 32+ byte value
- Set `DATABASE_URL` to production Postgres credentials
- Configure Redis with authentication if exposed
- Set appropriate CORS origins instead of `*`
- Enable HTTPS (terminate at load balancer or add TLS to Uvicorn)
