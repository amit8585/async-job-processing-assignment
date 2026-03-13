from __future__ import annotations

import math
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Job, JobStatus, User
from app.api.schemas import (
    JobListItem,
    JobResultResponse,
    JobStatusResponse,
    JobSubmitRequest,
    JobSubmitResponse,
    MessageResponse,
    PaginatedJobsResponse,
    TokenData,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserRegisterResponse,
)

# ─── Config ────────────────────────────────────────────────────────────────────

SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey-change-in-production-please")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
JOB_EXPIRY_HOURS = int(os.getenv("JOB_EXPIRY_HOURS", "24"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

router = APIRouter()


# ─── Auth Helpers ──────────────────────────────────────────────────────────────

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        token_data = TokenData(user_id=user_id)
    except JWTError:
        raise credentials_exception

    result = await db.execute(
        select(User).where(User.id == uuid.UUID(token_data.user_id))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    return user


# ─── Auth Routes ───────────────────────────────────────────────────────────────

@router.post(
    "/auth/register",
    response_model=UserRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Auth"],
    summary="Register a new user",
)
async def register(
    request: Request,
    payload: UserRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    # Check username uniqueness
    existing = await db.execute(
        select(User).where(
            (User.username == payload.username) | (User.email == payload.email)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already registered",
        )

    user = User(
        username=payload.username,
        email=payload.email,
        hashed_password=get_password_hash(payload.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post(
    "/auth/login",
    response_model=TokenResponse,
    tags=["Auth"],
    summary="Login and get JWT token",
)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.username == form_data.username)
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={"sub": str(user.id), "username": user.username},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return TokenResponse(access_token=access_token)


# ─── Job Routes ────────────────────────────────────────────────────────────────

@router.post(
    "/jobs/",
    response_model=JobSubmitResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Jobs"],
    summary="Submit a new job",
)
async def submit_job(
    request: Request,
    payload: JobSubmitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.tasks import process_job

    expires_at = datetime.utcnow() + timedelta(hours=JOB_EXPIRY_HOURS)

    job = Job(
        user_id=current_user.id,
        data=payload.data,
        operation=payload.operation,
        status=JobStatus.PENDING,
        expires_at=expires_at,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Dispatch Celery task
    process_job.delay(str(job.id))

    return JobSubmitResponse(job_id=job.id, status=job.status)


@router.get(
    "/jobs/",
    response_model=PaginatedJobsResponse,
    tags=["Jobs"],
    summary="List all jobs for the current user with pagination and filtering",
)
async def list_jobs(
    request: Request,
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=10, ge=1, le=100, description="Items per page"),
    status_filter: Optional[JobStatus] = Query(default=None, alias="status"),
    operation: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    base_query = select(Job).where(Job.user_id == current_user.id)

    if status_filter:
        base_query = base_query.where(Job.status == status_filter)
    if operation:
        base_query = base_query.where(Job.operation == operation)

    # Count total
    count_query = select(func.count()).select_from(base_query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Paginate
    offset = (page - 1) * page_size
    paginated_query = base_query.order_by(Job.created_at.desc()).offset(offset).limit(page_size)
    jobs_result = await db.execute(paginated_query)
    jobs = jobs_result.scalars().all()

    total_pages = max(1, math.ceil(total / page_size))

    items = [
        JobListItem(
            job_id=job.id,
            status=job.status,
            operation=job.operation,
            created_at=job.created_at,
            updated_at=job.updated_at,
            result=job.result,
        )
        for job in jobs
    ]

    return PaginatedJobsResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/jobs/{job_id}/status",
    response_model=JobStatusResponse,
    tags=["Jobs"],
    summary="Get job status",
)
async def get_job_status(
    request: Request,
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == current_user.id)
    )
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        operation=job.operation,
        created_at=job.created_at,
        updated_at=job.updated_at,
        expires_at=job.expires_at,
    )


@router.get(
    "/jobs/{job_id}/result",
    response_model=JobResultResponse,
    tags=["Jobs"],
    summary="Get job result",
)
async def get_job_result(
    request: Request,
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == current_user.id)
    )
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )

    message = None
    if job.status == JobStatus.PENDING:
        message = "Job is pending, please wait"
    elif job.status == JobStatus.IN_PROGRESS:
        message = "Job is currently being processed"
    elif job.status == JobStatus.FAILED:
        message = "Job failed"
    elif job.status == JobStatus.SUCCESS:
        message = "Job completed successfully"

    return JobResultResponse(
        job_id=job.id,
        status=job.status,
        operation=job.operation,
        result=job.result,
        message=message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.delete(
    "/jobs/{job_id}",
    response_model=MessageResponse,
    tags=["Jobs"],
    summary="Delete a job",
)
async def delete_job(
    request: Request,
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == current_user.id)
    )
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )

    await db.delete(job)
    await db.commit()
    return MessageResponse(message="Job deleted successfully")


@router.get(
    "/users/me",
    response_model=UserRegisterResponse,
    tags=["Users"],
    summary="Get current user info",
)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
):
    return current_user
