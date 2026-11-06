"""Background job schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobAcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    job_id: UUID
    status: JobStatus = JobStatus.QUEUED
    status_url: str


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    status: JobStatus
    job_type: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: int = 0
    result: Any = None
    error: str | None = None
