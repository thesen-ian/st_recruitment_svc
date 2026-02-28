from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from st_recruitment_svc.auth import get_current_user, require_company, require_job_seeker
from st_recruitment_svc.models.base import (
    get_db,
    Application,
    JobPosting,
    Interview,
    InterviewRescheduleRequest,
    InterviewStatus,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# Request/response schemas
class ProposeInterviewRequest(BaseModel):
    start_at: datetime
    duration_minutes: int
    format: str
    location_or_link: str
    interviewer_names: List[str]

    @field_validator("start_at")
    def tz_aware_start(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("start_at must be timezone-aware")
        return v

    @field_validator("duration_minutes")
    def positive_duration(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("duration_minutes must be > 0")
        return v

    @field_validator("format")
    def validate_format(cls, v: str) -> str:
        allowed = {"in-person", "phone", "video"}
        if v not in allowed:
            raise ValueError("format must be one of: in-person, phone, video")
        return v

    @field_validator("location_or_link")
    def non_empty_location(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("location_or_link must be non-empty")
        return v.strip()

    @field_validator("interviewer_names")
    def validate_interviewer_names(cls, v: List[str]) -> List[str]:
        if not v or len(v) == 0:
            raise ValueError("interviewer_names must be a non-empty list")
        cleaned: List[str] = []
        for name in v:
            if not isinstance(name, str):
                raise ValueError("each interviewer name must be a string")
            s = name.strip()
            if not s:
                raise ValueError("interviewer names must be non-empty after trimming")
            cleaned.append(s)
        return cleaned


class ProposeInterviewResponse(BaseModel):
    interview_id: str
    application_id: str
    start_at: str
    duration_minutes: int
    format: str
    location_or_link: str
    interviewer_names: List[str]
    status: str
    reschedule_count: int


class RescheduleRequestIn(BaseModel):
    reason: str
    preferred_times: List[datetime]

    @field_validator("reason")
    def non_empty_reason(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("reason must be non-empty")
        return v.strip()

    @field_validator("preferred_times")
    def validate_times(cls, v: List[datetime]) -> List[datetime]:
        if not v or len(v) == 0:
            raise ValueError("preferred_times must be a non-empty list")
        for dt in v:
            if dt.tzinfo is None:
                raise ValueError("preferred_times entries must be timezone-aware datetimes")
        return v


class RescheduleResponse(BaseModel):
    reschedule_request_id: str
    interview_id: str
    status: str
    reschedule_count: int


# Endpoint implementations
@router.post("/applications/{application_id}/interview/propose", response_model=ProposeInterviewResponse, status_code=status.HTTP_201_CREATED)
def propose_interview(
    application_id: str,
    payload: ProposeInterviewRequest,
    current_user=Depends(require_company()),
    db: Session = Depends(get_db),
) -> Any:
    # Company-only. Verify application exists and belongs to the company via JobPosting.
    try:
        stmt = select(Application).where(Application.id == application_id)
        app_row = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading application %s", application_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if app_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="application not found")

    try:
        job_stmt = select(JobPosting).where(JobPosting.id == app_row.job_id)
        job = db.execute(job_stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading job for application %s", application_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if job is None or getattr(job, "company_id", None) != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not owner of application")

    # Persist interview
    try:
        interview = Interview(
            application_id=app_row.id,
            start_at=payload.start_at,
            duration_minutes=payload.duration_minutes,
            format=payload.format,
            location_or_link=payload.location_or_link,
            interviewer_names=payload.interviewer_names,
            status=InterviewStatus.proposed,
            reschedule_count=0,
        )
        db.add(interview)
        db.commit()
        db.refresh(interview)
    except Exception as e:
        logger.exception("Failed to create interview for application %s: %s", application_id, e)
        try:
            db.rollback()
        except Exception:
            logger.exception("rollback failed after interview create failure")
        raise HTTPException(status_code=500, detail="failed to create interview")

    resp = ProposeInterviewResponse(
        interview_id=interview.id,
        application_id=interview.application_id,
        start_at=interview.start_at.isoformat(),
        duration_minutes=interview.duration_minutes,
        format=interview.format,
        location_or_link=interview.location_or_link,
        interviewer_names=interview.interviewer_names,
        status=interview.status.value,
        reschedule_count=interview.reschedule_count,
    )
    return resp.model_dump() if hasattr(resp, "model_dump") else resp.dict()


@router.post("/interviews/{interview_id}/accept")
def accept_interview(
    interview_id: str,
    current_user=Depends(require_job_seeker()),
    db: Session = Depends(get_db),
) -> Any:
    try:
        stmt = select(Interview).options(selectinload(Interview.application)).where(Interview.id == interview_id)
        interview = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading interview %s", interview_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if interview is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="interview not found")

    # Verify job seeker ownership via application
    try:
        app = interview.application
    except Exception:
        logger.exception("Failed to access application for interview %s", interview_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if getattr(app, "job_seeker_user_id", None) != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    # Reject if cancelled
    status_val = getattr(interview.status, 'value', interview.status)
    if status_val == InterviewStatus.cancelled.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="interview cancelled")

    try:
        interview.status = InterviewStatus.accepted
        try:
            interview.updated_at = datetime.now(tz=timezone.utc)
        except Exception:
            pass
        db.add(interview)
        db.commit()
        db.refresh(interview)
    except Exception as e:
        logger.exception("Failed to accept interview %s: %s", interview_id, e)
        try:
            db.rollback()
        except Exception:
            logger.exception("rollback failed after accept failure")
        raise HTTPException(status_code=500, detail="failed to accept interview")

    return {"interview_id": interview.id, "status": interview.status.value}


@router.post("/interviews/{interview_id}/reschedule-request", response_model=RescheduleResponse)
def reschedule_request(
    interview_id: str,
    payload: RescheduleRequestIn,
    current_user=Depends(require_job_seeker()),
    db: Session = Depends(get_db),
) -> Any:
    try:
        stmt = select(Interview).options(selectinload(Interview.application)).where(Interview.id == interview_id)
        interview = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading interview %s", interview_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if interview is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="interview not found")

    # Verify job seeker ownership via application
    if getattr(interview.application, "job_seeker_user_id", None) != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    status_val = getattr(interview.status, 'value', interview.status)
    if status_val == InterviewStatus.cancelled.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="interview cancelled")

    # Enforce reschedule cap
    try:
        current_count = int(getattr(interview, "reschedule_count", 0) or 0)
    except Exception:
        current_count = 0
    if current_count >= 3:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="reschedule request limit reached")

    try:
        # Store preferred_times as ISO strings to ensure JSON serializability
        preferred_times_serialized = [dt.isoformat() for dt in payload.preferred_times]

        req = InterviewRescheduleRequest(
            interview_id=interview.id,
            requested_by_user_id=current_user.id,
            reason=payload.reason,
            preferred_times=preferred_times_serialized,
        )
        # update interview
        interview.reschedule_count = current_count + 1
        interview.status = InterviewStatus.reschedule_requested

        db.add(req)
        db.add(interview)
        db.commit()
        db.refresh(req)
        db.refresh(interview)
    except Exception as e:
        logger.exception("Failed to create reschedule request for interview %s: %s", interview_id, e)
        try:
            db.rollback()
        except Exception:
            logger.exception("rollback failed after reschedule create failure")
        raise HTTPException(status_code=500, detail="failed to create reschedule request")

    resp = RescheduleResponse(
        reschedule_request_id=req.id,
        interview_id=interview.id,
        status=interview.status.value,
        reschedule_count=interview.reschedule_count,
    )
    return resp.model_dump() if hasattr(resp, "model_dump") else resp.dict()


@router.post("/interviews/{interview_id}/cancel")
def cancel_interview(
    interview_id: str,
    current_user=Depends(require_company()),
    db: Session = Depends(get_db),
) -> Any:
    try:
        stmt = select(Interview).options(selectinload(Interview.application)).where(Interview.id == interview_id)
        interview = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading interview %s", interview_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if interview is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="interview not found")

    # Verify owner via application -> JobPosting
    try:
        app = interview.application
        job_stmt = select(JobPosting).where(JobPosting.id == app.job_id)
        job = db.execute(job_stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading job for interview %s", interview_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if job is None or getattr(job, "company_id", None) != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not owner of interview")

    try:
        interview.status = InterviewStatus.cancelled
        try:
            interview.updated_at = datetime.now(tz=timezone.utc)
        except Exception:
            pass
        db.add(interview)
        db.commit()
        db.refresh(interview)
    except Exception as e:
        logger.exception("Failed to cancel interview %s: %s", interview_id, e)
        try:
            db.rollback()
        except Exception:
            logger.exception("rollback failed after cancel failure")
        raise HTTPException(status_code=500, detail="failed to cancel interview")

    return {"interview_id": interview.id, "status": interview.status.value}
