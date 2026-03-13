import os
import time
import uuid

from celery import Celery
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DATABASE_URL_SYNC = os.getenv(
    "DATABASE_URL_SYNC",
    "postgresql+psycopg2://postgres:postgres@db:5432/jobsdb"
)

celery_app = Celery("tasks", broker=REDIS_URL, backend=REDIS_URL)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "cleanup-expired-jobs-every-hour": {
            "task": "cleanup_expired_jobs",
            "schedule": 3600.0,  # every hour
        },
    },
)

engine_sync = create_engine(DATABASE_URL_SYNC, pool_pre_ping=True)
SyncSessionLocal = sessionmaker(bind=engine_sync)


@celery_app.task(name="process_job", bind=True, max_retries=3)
def process_job(self, job_id: str):
    """
    Process a job identified by job_id.
    Supports operations: square_sum, cube_sum.
    """
    # Import here to avoid circular imports at module load time
    from app.models import Job, JobStatus

    # Step 1: Mark job as IN_PROGRESS
    with SyncSessionLocal() as session:
        job = session.get(Job, uuid.UUID(job_id))
        if not job:
            return {"error": f"Job {job_id} not found"}
        job.status = JobStatus.IN_PROGRESS
        session.commit()

    # Simulate processing delay
    time.sleep(2)

    # Step 2: Compute result
    try:
        with SyncSessionLocal() as session:
            job = session.get(Job, uuid.UUID(job_id))
            if not job:
                return {"error": f"Job {job_id} not found"}

            numbers = job.data
            operation = job.operation

            if not isinstance(numbers, list):
                raise ValueError("Job data must be a list of numbers")

            if operation == "square_sum":
                result = sum(x ** 2 for x in numbers)
            elif operation == "cube_sum":
                result = sum(x ** 3 for x in numbers)
            else:
                raise ValueError(f"Unknown operation: {operation}")

            job.result = {"value": result, "operation": operation, "input": numbers}
            job.status = JobStatus.SUCCESS
            session.commit()

        return {"status": "SUCCESS", "result": result}

    except Exception as exc:
        with SyncSessionLocal() as session:
            job = session.get(Job, uuid.UUID(job_id))
            if job:
                job.status = JobStatus.FAILED
                job.result = {"error": str(exc)}
                session.commit()
        raise self.retry(exc=exc, countdown=5) if self.request.retries < self.max_retries else exc


@celery_app.task(name="cleanup_expired_jobs")
def cleanup_expired_jobs():
    """Periodic task: delete jobs that have passed their expires_at timestamp."""
    from datetime import datetime
    from sqlalchemy import delete as sa_delete
    from app.models import Job

    with SyncSessionLocal() as session:
        now = datetime.utcnow()
        stmt = sa_delete(Job).where(Job.expires_at < now)
        result = session.execute(stmt)
        session.commit()
        deleted = result.rowcount
        return {"deleted": deleted, "timestamp": now.isoformat()}
