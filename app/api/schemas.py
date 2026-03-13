from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models import JobStatus


# ─── Auth Schemas ──────────────────────────────────────────────────────────────

class UserRegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=100)


class UserRegisterResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    created_at: datetime

    model_config = {"from_attributes": True}


class UserLoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: Optional[str] = None
    username: Optional[str] = None


# ─── Job Schemas ───────────────────────────────────────────────────────────────

class JobSubmitRequest(BaseModel):
    data: List[float] = Field(..., min_length=1, max_length=1000)
    operation: str = Field(..., pattern="^(square_sum|cube_sum)$")

    @field_validator("data")
    @classmethod
    def validate_data(cls, v: List[float]) -> List[float]:
        if not v:
            raise ValueError("data list must not be empty")
        return v


class JobSubmitResponse(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    message: str = "Job submitted successfully"

    model_config = {"from_attributes": True}


class JobStatusResponse(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    operation: str
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class JobResultResponse(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    operation: str
    result: Optional[Any] = None
    message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobListItem(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    operation: str
    created_at: datetime
    updated_at: datetime
    result: Optional[Any] = None

    model_config = {"from_attributes": True}


class PaginatedJobsResponse(BaseModel):
    items: List[JobListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


# ─── Generic ───────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str
