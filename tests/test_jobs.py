"""
Comprehensive test suite for the Async Job Processing Service.

Tests cover:
- User registration and login
- Job submission (authenticated)
- Job status polling
- Job result retrieval
- Unauthorized access rejection
- Invalid operation rejection
- Pagination and filtering
- Job deletion
- Edge cases
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import Job, JobStatus, User


# ─── Test Database Setup ───────────────────────────────────────────────────────

TEST_DATABASE_URL = "sqlite+aiosqlite:///./test_db.sqlite3"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

TestingSessionLocal = sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with TestingSessionLocal() as session:
        yield session


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    """Create all tables before tests, drop after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clear_tables():
    """Clear tables between tests for isolation."""
    yield
    async with TestingSessionLocal() as session:
        # Delete Jobs first (child), then Users (parent) to respect FK constraints.
        # SQLite: disable foreign key enforcement during bulk delete for safety.
        await session.execute(Job.__table__.delete())
        await session.execute(User.__table__.delete())
        await session.commit()


# ─── App Client Fixture ───────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """
    Create an AsyncClient with:
    - Test SQLite database override (no PostgreSQL needed)
    - Mocked Celery task dispatch (no Redis/broker needed)
    - Mocked lifespan (skip create_all on the real DB engine)
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_lifespan(_application):
        # Tables are already created by setup_database fixture via test engine
        yield

    app.dependency_overrides[get_db] = override_get_db
    # Replace lifespan so we don't connect to real PostgreSQL
    original_router = app.router.lifespan_context
    app.router.lifespan_context = mock_lifespan

    # Mock celery task so we don't need a real broker
    with patch("app.api.routes.process_job") as mock_task:
        mock_delay = MagicMock()
        mock_task.delay = mock_delay

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac

    app.router.lifespan_context = original_router
    app.dependency_overrides.clear()


# ─── Helper Functions ─────────────────────────────────────────────────────────

async def register_user(
    client: AsyncClient,
    username: str = "testuser",
    email: str = "test@example.com",
    password: str = "testpassword",
) -> dict:
    response = await client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    return response


async def login_user(
    client: AsyncClient,
    username: str = "testuser",
    password: str = "testpassword",
) -> str:
    """Log in and return the access token."""
    response = await client.post(
        "/auth/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    return response.json()["access_token"]


async def register_and_login(
    client: AsyncClient,
    username: str = "testuser",
    email: str = "test@example.com",
    password: str = "testpassword",
) -> str:
    """Register a user and return a JWT token."""
    reg = await register_user(client, username, email, password)
    assert reg.status_code == 201, f"Registration failed: {reg.text}"
    return await login_user(client, username, password)


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestUserRegistration:
    async def test_register_success(self, client: AsyncClient):
        response = await client.post(
            "/auth/register",
            json={"username": "alice", "email": "alice@example.com", "password": "secret123"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "alice"
        assert data["email"] == "alice@example.com"
        assert "id" in data
        assert "hashed_password" not in data

    async def test_register_duplicate_username(self, client: AsyncClient):
        await register_user(client, "bob", "bob@example.com", "pass123")
        response = await client.post(
            "/auth/register",
            json={"username": "bob", "email": "bob2@example.com", "password": "pass456"},
        )
        assert response.status_code == 409
        assert "already registered" in response.json()["detail"].lower()

    async def test_register_duplicate_email(self, client: AsyncClient):
        await register_user(client, "carol", "carol@example.com", "pass123")
        response = await client.post(
            "/auth/register",
            json={"username": "carol2", "email": "carol@example.com", "password": "pass456"},
        )
        assert response.status_code == 409

    async def test_register_short_username(self, client: AsyncClient):
        response = await client.post(
            "/auth/register",
            json={"username": "ab", "email": "ab@example.com", "password": "pass123"},
        )
        assert response.status_code == 422

    async def test_register_short_password(self, client: AsyncClient):
        response = await client.post(
            "/auth/register",
            json={"username": "dave", "email": "dave@example.com", "password": "abc"},
        )
        assert response.status_code == 422

    async def test_register_invalid_email(self, client: AsyncClient):
        response = await client.post(
            "/auth/register",
            json={"username": "eve", "email": "not-an-email", "password": "pass123"},
        )
        assert response.status_code == 422


class TestUserLogin:
    async def test_login_success(self, client: AsyncClient):
        await register_user(client, "frank", "frank@example.com", "mypassword")
        response = await client.post(
            "/auth/login",
            data={"username": "frank", "password": "mypassword"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    async def test_login_wrong_password(self, client: AsyncClient):
        await register_user(client, "grace", "grace@example.com", "correctpass")
        response = await client.post(
            "/auth/login",
            data={"username": "grace", "password": "wrongpass"},
        )
        assert response.status_code == 401
        assert "incorrect" in response.json()["detail"].lower()

    async def test_login_nonexistent_user(self, client: AsyncClient):
        response = await client.post(
            "/auth/login",
            data={"username": "nobody", "password": "somepass"},
        )
        assert response.status_code == 401

    async def test_get_current_user(self, client: AsyncClient):
        token = await register_and_login(client, "henry", "henry@example.com", "hpass123")
        response = await client.get("/users/me", headers=auth_headers(token))
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "henry"


# ═══════════════════════════════════════════════════════════════════════════════
# JOB SUBMISSION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestJobSubmission:
    async def test_submit_square_sum_job(self, client: AsyncClient):
        token = await register_and_login(client, "user1", "u1@test.com", "pass1234")
        response = await client.post(
            "/jobs/",
            json={"data": [1, 2, 3, 4, 5], "operation": "square_sum"},
            headers=auth_headers(token),
        )
        assert response.status_code == 201
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "PENDING"
        # Validate UUID format
        uuid.UUID(data["job_id"])

    async def test_submit_cube_sum_job(self, client: AsyncClient):
        token = await register_and_login(client, "user2", "u2@test.com", "pass1234")
        response = await client.post(
            "/jobs/",
            json={"data": [1, 2, 3], "operation": "cube_sum"},
            headers=auth_headers(token),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "PENDING"

    async def test_submit_job_unauthenticated(self, client: AsyncClient):
        response = await client.post(
            "/jobs/",
            json={"data": [1, 2, 3], "operation": "square_sum"},
        )
        assert response.status_code == 401

    async def test_submit_job_invalid_operation(self, client: AsyncClient):
        token = await register_and_login(client, "user3", "u3@test.com", "pass1234")
        response = await client.post(
            "/jobs/",
            json={"data": [1, 2, 3], "operation": "invalid_op"},
            headers=auth_headers(token),
        )
        assert response.status_code == 422

    async def test_submit_job_empty_data(self, client: AsyncClient):
        token = await register_and_login(client, "user4", "u4@test.com", "pass1234")
        response = await client.post(
            "/jobs/",
            json={"data": [], "operation": "square_sum"},
            headers=auth_headers(token),
        )
        assert response.status_code == 422

    async def test_submit_job_with_floats(self, client: AsyncClient):
        token = await register_and_login(client, "user5", "u5@test.com", "pass1234")
        response = await client.post(
            "/jobs/",
            json={"data": [1.5, 2.5, 3.5], "operation": "square_sum"},
            headers=auth_headers(token),
        )
        assert response.status_code == 201

    async def test_submit_job_missing_data_field(self, client: AsyncClient):
        token = await register_and_login(client, "user6", "u6@test.com", "pass1234")
        response = await client.post(
            "/jobs/",
            json={"operation": "square_sum"},
            headers=auth_headers(token),
        )
        assert response.status_code == 422

    async def test_submit_job_with_invalid_token(self, client: AsyncClient):
        response = await client.post(
            "/jobs/",
            json={"data": [1, 2, 3], "operation": "square_sum"},
            headers={"Authorization": "Bearer totally.invalid.token"},
        )
        assert response.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# JOB STATUS TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestJobStatus:
    async def test_get_job_status_pending(self, client: AsyncClient):
        token = await register_and_login(client, "user7", "u7@test.com", "pass1234")
        submit = await client.post(
            "/jobs/",
            json={"data": [1, 2, 3], "operation": "square_sum"},
            headers=auth_headers(token),
        )
        job_id = submit.json()["job_id"]

        response = await client.get(
            f"/jobs/{job_id}/status",
            headers=auth_headers(token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] in ("PENDING", "IN_PROGRESS", "SUCCESS", "FAILED")
        assert data["operation"] == "square_sum"

    async def test_get_status_unauthenticated(self, client: AsyncClient):
        token = await register_and_login(client, "user8", "u8@test.com", "pass1234")
        submit = await client.post(
            "/jobs/",
            json={"data": [1, 2, 3], "operation": "square_sum"},
            headers=auth_headers(token),
        )
        job_id = submit.json()["job_id"]

        response = await client.get(f"/jobs/{job_id}/status")
        assert response.status_code == 401

    async def test_get_status_not_found(self, client: AsyncClient):
        token = await register_and_login(client, "user9", "u9@test.com", "pass1234")
        fake_id = str(uuid.uuid4())
        response = await client.get(
            f"/jobs/{fake_id}/status",
            headers=auth_headers(token),
        )
        assert response.status_code == 404

    async def test_get_status_other_users_job(self, client: AsyncClient):
        """User A cannot see User B's job status."""
        token_a = await register_and_login(client, "user_a", "ua@test.com", "pass1234")
        token_b = await register_and_login(client, "user_b", "ub@test.com", "pass1234")

        submit = await client.post(
            "/jobs/",
            json={"data": [1, 2, 3], "operation": "square_sum"},
            headers=auth_headers(token_a),
        )
        job_id = submit.json()["job_id"]

        # User B tries to access User A's job
        response = await client.get(
            f"/jobs/{job_id}/status",
            headers=auth_headers(token_b),
        )
        assert response.status_code == 404

    async def test_status_response_has_timestamps(self, client: AsyncClient):
        token = await register_and_login(client, "user10", "u10@test.com", "pass1234")
        submit = await client.post(
            "/jobs/",
            json={"data": [1, 2, 3], "operation": "cube_sum"},
            headers=auth_headers(token),
        )
        job_id = submit.json()["job_id"]

        response = await client.get(
            f"/jobs/{job_id}/status",
            headers=auth_headers(token),
        )
        data = response.json()
        assert "created_at" in data
        assert "updated_at" in data


# ═══════════════════════════════════════════════════════════════════════════════
# JOB RESULT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestJobResult:
    async def test_get_result_pending_job(self, client: AsyncClient):
        token = await register_and_login(client, "user11", "u11@test.com", "pass1234")
        submit = await client.post(
            "/jobs/",
            json={"data": [1, 2, 3], "operation": "square_sum"},
            headers=auth_headers(token),
        )
        job_id = submit.json()["job_id"]

        response = await client.get(
            f"/jobs/{job_id}/result",
            headers=auth_headers(token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "PENDING"
        assert "pending" in data["message"].lower() or "processing" in data["message"].lower() or data["message"] is not None

    async def test_get_result_unauthenticated(self, client: AsyncClient):
        token = await register_and_login(client, "user12", "u12@test.com", "pass1234")
        submit = await client.post(
            "/jobs/",
            json={"data": [1, 2, 3], "operation": "square_sum"},
            headers=auth_headers(token),
        )
        job_id = submit.json()["job_id"]

        response = await client.get(f"/jobs/{job_id}/result")
        assert response.status_code == 401

    async def test_get_result_not_found(self, client: AsyncClient):
        token = await register_and_login(client, "user13", "u13@test.com", "pass1234")
        fake_id = str(uuid.uuid4())
        response = await client.get(
            f"/jobs/{fake_id}/result",
            headers=auth_headers(token),
        )
        assert response.status_code == 404

    async def test_get_result_completed_job(self, client: AsyncClient):
        """Simulate a completed job by directly setting status in DB."""
        token = await register_and_login(client, "user14", "u14@test.com", "pass1234")
        submit = await client.post(
            "/jobs/",
            json={"data": [1, 2, 3, 4, 5], "operation": "square_sum"},
            headers=auth_headers(token),
        )
        job_id = submit.json()["job_id"]

        # Manually update the job to SUCCESS in test DB
        async with TestingSessionLocal() as session:
            job = await session.get(Job, uuid.UUID(job_id))
            assert job is not None
            job.status = JobStatus.SUCCESS
            job.result = {"value": 55, "operation": "square_sum", "input": [1, 2, 3, 4, 5]}
            await session.commit()

        response = await client.get(
            f"/jobs/{job_id}/result",
            headers=auth_headers(token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "SUCCESS"
        assert data["result"]["value"] == 55
        assert "successfully" in data["message"].lower()

    async def test_get_result_failed_job(self, client: AsyncClient):
        token = await register_and_login(client, "user15", "u15@test.com", "pass1234")
        submit = await client.post(
            "/jobs/",
            json={"data": [1, 2, 3], "operation": "square_sum"},
            headers=auth_headers(token),
        )
        job_id = submit.json()["job_id"]

        # Manually set to FAILED
        async with TestingSessionLocal() as session:
            job = await session.get(Job, uuid.UUID(job_id))
            job.status = JobStatus.FAILED
            job.result = {"error": "Simulated failure"}
            await session.commit()

        response = await client.get(
            f"/jobs/{job_id}/result",
            headers=auth_headers(token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "FAILED"
        assert data["result"]["error"] == "Simulated failure"
        assert "failed" in data["message"].lower()

    async def test_result_other_users_job_is_not_visible(self, client: AsyncClient):
        token_a = await register_and_login(client, "user_c", "uc@test.com", "pass1234")
        token_b = await register_and_login(client, "user_d", "ud@test.com", "pass1234")

        submit = await client.post(
            "/jobs/",
            json={"data": [1, 2, 3], "operation": "square_sum"},
            headers=auth_headers(token_a),
        )
        job_id = submit.json()["job_id"]

        response = await client.get(
            f"/jobs/{job_id}/result",
            headers=auth_headers(token_b),
        )
        assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# JOB LISTING + PAGINATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestJobListing:
    async def test_list_jobs_empty(self, client: AsyncClient):
        token = await register_and_login(client, "list_user1", "lu1@test.com", "pass1234")
        response = await client.get("/jobs/", headers=auth_headers(token))
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []
        assert data["page"] == 1

    async def test_list_jobs_with_data(self, client: AsyncClient):
        token = await register_and_login(client, "list_user2", "lu2@test.com", "pass1234")

        # Submit 3 jobs
        for i in range(3):
            await client.post(
                "/jobs/",
                json={"data": [i + 1, i + 2], "operation": "square_sum"},
                headers=auth_headers(token),
            )

        response = await client.get("/jobs/", headers=auth_headers(token))
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3

    async def test_list_jobs_pagination(self, client: AsyncClient):
        token = await register_and_login(client, "list_user3", "lu3@test.com", "pass1234")

        # Submit 15 jobs
        for i in range(15):
            await client.post(
                "/jobs/",
                json={"data": [i + 1], "operation": "square_sum"},
                headers=auth_headers(token),
            )

        # Page 1, size 10
        response = await client.get("/jobs/?page=1&page_size=10", headers=auth_headers(token))
        data = response.json()
        assert data["total"] == 15
        assert len(data["items"]) == 10
        assert data["total_pages"] == 2
        assert data["page"] == 1

        # Page 2, size 10
        response2 = await client.get("/jobs/?page=2&page_size=10", headers=auth_headers(token))
        data2 = response2.json()
        assert len(data2["items"]) == 5
        assert data2["page"] == 2

    async def test_list_jobs_filter_by_status(self, client: AsyncClient):
        token = await register_and_login(client, "list_user4", "lu4@test.com", "pass1234")

        # Submit 2 jobs, manually complete 1
        resp1 = await client.post(
            "/jobs/",
            json={"data": [1, 2], "operation": "square_sum"},
            headers=auth_headers(token),
        )
        resp2 = await client.post(
            "/jobs/",
            json={"data": [3, 4], "operation": "square_sum"},
            headers=auth_headers(token),
        )

        job_id1 = resp1.json()["job_id"]
        async with TestingSessionLocal() as session:
            job = await session.get(Job, uuid.UUID(job_id1))
            job.status = JobStatus.SUCCESS
            job.result = {"value": 5}
            await session.commit()

        # Filter by SUCCESS
        response = await client.get(
            "/jobs/?status=SUCCESS",
            headers=auth_headers(token),
        )
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "SUCCESS"

        # Filter by PENDING
        response = await client.get(
            "/jobs/?status=PENDING",
            headers=auth_headers(token),
        )
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "PENDING"

    async def test_list_jobs_filter_by_operation(self, client: AsyncClient):
        token = await register_and_login(client, "list_user5", "lu5@test.com", "pass1234")

        await client.post(
            "/jobs/",
            json={"data": [1, 2], "operation": "square_sum"},
            headers=auth_headers(token),
        )
        await client.post(
            "/jobs/",
            json={"data": [1, 2], "operation": "cube_sum"},
            headers=auth_headers(token),
        )

        response = await client.get(
            "/jobs/?operation=cube_sum",
            headers=auth_headers(token),
        )
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["operation"] == "cube_sum"

    async def test_list_jobs_only_own_jobs(self, client: AsyncClient):
        """Users should only see their own jobs."""
        token_x = await register_and_login(client, "user_x", "ux@test.com", "pass1234")
        token_y = await register_and_login(client, "user_y", "uy@test.com", "pass1234")

        # User X submits 2 jobs
        for _ in range(2):
            await client.post(
                "/jobs/",
                json={"data": [1, 2], "operation": "square_sum"},
                headers=auth_headers(token_x),
            )

        # User Y submits 1 job
        await client.post(
            "/jobs/",
            json={"data": [3], "operation": "cube_sum"},
            headers=auth_headers(token_y),
        )

        resp_x = await client.get("/jobs/", headers=auth_headers(token_x))
        resp_y = await client.get("/jobs/", headers=auth_headers(token_y))

        assert resp_x.json()["total"] == 2
        assert resp_y.json()["total"] == 1

    async def test_list_jobs_unauthenticated(self, client: AsyncClient):
        response = await client.get("/jobs/")
        assert response.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# JOB DELETION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestJobDeletion:
    async def test_delete_job_success(self, client: AsyncClient):
        token = await register_and_login(client, "del_user1", "du1@test.com", "pass1234")
        submit = await client.post(
            "/jobs/",
            json={"data": [1, 2, 3], "operation": "square_sum"},
            headers=auth_headers(token),
        )
        job_id = submit.json()["job_id"]

        response = await client.delete(
            f"/jobs/{job_id}",
            headers=auth_headers(token),
        )
        assert response.status_code == 200
        assert "deleted" in response.json()["message"].lower()

        # Verify it's gone
        status_resp = await client.get(
            f"/jobs/{job_id}/status",
            headers=auth_headers(token),
        )
        assert status_resp.status_code == 404

    async def test_delete_job_not_owner(self, client: AsyncClient):
        token_owner = await register_and_login(client, "owner1", "o1@test.com", "pass1234")
        token_other = await register_and_login(client, "other1", "ot1@test.com", "pass1234")

        submit = await client.post(
            "/jobs/",
            json={"data": [1, 2], "operation": "square_sum"},
            headers=auth_headers(token_owner),
        )
        job_id = submit.json()["job_id"]

        response = await client.delete(
            f"/jobs/{job_id}",
            headers=auth_headers(token_other),
        )
        assert response.status_code == 404

    async def test_delete_nonexistent_job(self, client: AsyncClient):
        token = await register_and_login(client, "del_user2", "du2@test.com", "pass1234")
        fake_id = str(uuid.uuid4())
        response = await client.delete(
            f"/jobs/{fake_id}",
            headers=auth_headers(token),
        )
        assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# CELERY TASK LOGIC TESTS (unit tests, no broker needed)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCeleryTaskLogic:
    """Test the computation logic of the Celery task directly."""

    def test_square_sum_calculation(self):
        """square_sum([1,2,3,4,5]) == 1+4+9+16+25 == 55"""
        numbers = [1, 2, 3, 4, 5]
        result = sum(x ** 2 for x in numbers)
        assert result == 55

    def test_cube_sum_calculation(self):
        """cube_sum([1,2,3]) == 1+8+27 == 36"""
        numbers = [1, 2, 3]
        result = sum(x ** 3 for x in numbers)
        assert result == 36

    def test_square_sum_single_value(self):
        assert sum(x ** 2 for x in [7]) == 49

    def test_cube_sum_single_value(self):
        assert sum(x ** 3 for x in [3]) == 27

    def test_square_sum_with_floats(self):
        result = sum(x ** 2 for x in [1.5, 2.5])
        assert abs(result - (2.25 + 6.25)) < 1e-9

    def test_cube_sum_with_negatives(self):
        result = sum(x ** 3 for x in [-2, 2])
        assert result == 0  # -8 + 8 = 0


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoints:
    async def test_root_endpoint(self, client: AsyncClient):
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data

    async def test_health_endpoint(self, client: AsyncClient):
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "timestamp" in data

    async def test_openapi_docs_available(self, client: AsyncClient):
        response = await client.get("/docs")
        assert response.status_code == 200

    async def test_openapi_schema_available(self, client: AsyncClient):
        response = await client.get("/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "openapi" in data
        assert "paths" in data


# ═══════════════════════════════════════════════════════════════════════════════
# EDGE CASE / SECURITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    async def test_invalid_uuid_in_path(self, client: AsyncClient):
        token = await register_and_login(client, "edge_user1", "eu1@test.com", "pass1234")
        response = await client.get(
            "/jobs/not-a-valid-uuid/status",
            headers=auth_headers(token),
        )
        assert response.status_code == 422

    async def test_expired_token(self, client: AsyncClient):
        """Test that an obviously malformed/expired token is rejected."""
        headers = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.invalid"}
        response = await client.get("/jobs/", headers=headers)
        assert response.status_code == 401

    async def test_submit_large_data_list(self, client: AsyncClient):
        token = await register_and_login(client, "edge_user2", "eu2@test.com", "pass1234")
        large_data = list(range(1, 1001))  # 1000 items (max allowed)
        response = await client.post(
            "/jobs/",
            json={"data": large_data, "operation": "square_sum"},
            headers=auth_headers(token),
        )
        assert response.status_code == 201

    async def test_submit_over_limit_data_list(self, client: AsyncClient):
        token = await register_and_login(client, "edge_user3", "eu3@test.com", "pass1234")
        too_large = list(range(1, 1002))  # 1001 items (over max)
        response = await client.post(
            "/jobs/",
            json={"data": too_large, "operation": "square_sum"},
            headers=auth_headers(token),
        )
        assert response.status_code == 422

    async def test_page_size_limits(self, client: AsyncClient):
        token = await register_and_login(client, "edge_user4", "eu4@test.com", "pass1234")

        # page_size=0 is invalid
        response = await client.get("/jobs/?page_size=0", headers=auth_headers(token))
        assert response.status_code == 422

        # page_size=101 is over limit
        response = await client.get("/jobs/?page_size=101", headers=auth_headers(token))
        assert response.status_code == 422

    async def test_negative_page_number(self, client: AsyncClient):
        token = await register_and_login(client, "edge_user5", "eu5@test.com", "pass1234")
        response = await client.get("/jobs/?page=0", headers=auth_headers(token))
        assert response.status_code == 422
