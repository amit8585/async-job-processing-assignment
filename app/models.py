import uuid
from sqlalchemy import Column, String, JSON, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy import Uuid  # cross-dialect UUID type (SQLAlchemy 2.0+)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.db import Base


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class User(Base):
    __tablename__ = "users"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String, unique=True, nullable=False, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    jobs = relationship("Job", back_populates="owner", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    data = Column(JSON, nullable=False)
    operation = Column(String, nullable=False)
    status = Column(SAEnum(JobStatus), default=JobStatus.PENDING, nullable=False, index=True)
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)

    owner = relationship("User", back_populates="jobs")
