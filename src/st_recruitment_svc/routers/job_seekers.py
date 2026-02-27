from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from st_recruitment_svc.auth import require_job_seeker
from st_recruitment_svc.models.base import JobSeekerProfile, get_db, User

logger = logging.getLogger(__name__)

router = APIRouter()


# Pydantic schemas for profile input/output
class ProfileIn(BaseModel):
    full_name: str
    email: str
    phone: Optional[str] = None
    location: Optional[str] = None
    summary: Optional[str] = None
    experiences: List[Dict[str, Any]] = Field(default_factory=list)
    education: List[Dict[str, Any]] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    languages: List[Dict[str, Any]] = Field(default_factory=list)
    certifications: List[Dict[str, Any]] = Field(default_factory=list)


class ProfileOut(ProfileIn):
    id: str
    user_id: str
    created_at: str
    updated_at: str


def _serialize_profile(p: JobSeekerProfile) -> Dict[str, Any]:
    # Convert SQLAlchemy model to JSON-serializable dict
    return {
        "id": p.id,
        "user_id": p.user_id,
        "full_name": p.full_name,
        "email": p.email,
        "phone": p.phone,
        "location": p.location,
        "summary": p.summary,
        "experiences": p.experiences or [],
        "education": p.education or [],
        "skills": p.skills or [],
        "languages": p.languages or [],
        "certifications": p.certifications or [],
        "created_at": p.created_at.isoformat() if hasattr(p, "created_at") and p.created_at is not None else None,
        "updated_at": p.updated_at.isoformat() if hasattr(p, "updated_at") and p.updated_at is not None else None,
    }


@router.get("/job-seekers/me", response_model=ProfileOut)
def get_my_profile(
    current_user: User = Depends(require_job_seeker()),
    db: Session = Depends(get_db),
) -> Any:
    """Return the authenticated job seeker's profile or 404 if missing."""
    try:
        stmt = select(JobSeekerProfile).where(JobSeekerProfile.user_id == current_user.id)
        profile = db.execute(stmt).scalars().first()
    except Exception:
        logger.error("DB error loading job seeker profile for user %s", current_user.id, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")

    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="profile not found")

    return _serialize_profile(profile)


@router.put("/job-seekers/me", response_model=ProfileOut)
def upsert_my_profile(
    payload: ProfileIn,
    current_user: User = Depends(require_job_seeker()),
    db: Session = Depends(get_db),
) -> Any:
    """Create or update the authenticated job seeker's profile.

    Performs lightweight validation and upsert semantics.
    """
    # Basic required fields validation
    if not payload.full_name or not payload.full_name.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="full_name is required")
    if not payload.email or not payload.email.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="email is required")

    try:
        stmt = select(JobSeekerProfile).where(JobSeekerProfile.user_id == current_user.id)
        profile = db.execute(stmt).scalars().first()
    except Exception:
        logger.error("DB error loading job seeker profile for upsert for user %s", current_user.id, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")

    now = datetime.now(tz=timezone.utc)

    try:
        if profile is None:
            profile = JobSeekerProfile(
                user_id=current_user.id,
                full_name=payload.full_name,
                email=payload.email,
                phone=payload.phone,
                location=payload.location,
                summary=payload.summary,
                experiences=payload.experiences or [],
                education=payload.education or [],
                skills=payload.skills or [],
                languages=payload.languages or [],
                certifications=payload.certifications or [],
            )
            db.add(profile)
            db.commit()
            db.refresh(profile)
        else:
            # Update fields
            profile.full_name = payload.full_name
            profile.email = payload.email
            profile.phone = payload.phone
            profile.location = payload.location
            profile.summary = payload.summary
            profile.experiences = payload.experiences or []
            profile.education = payload.education or []
            profile.skills = payload.skills or []
            profile.languages = payload.languages or []
            profile.certifications = payload.certifications or []
            # Touch updated_at if present; rely on DB server_default otherwise
            try:
                if hasattr(profile, "updated_at"):
                    profile.updated_at = now
            except Exception:
                logger.debug("Could not set updated_at on profile %s", getattr(profile, "id", "<unknown>"))
            db.commit()
            db.refresh(profile)
    except HTTPException:
        raise
    except Exception:
        logger.error("Failed to upsert job seeker profile for user %s", current_user.id, exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="failed to save profile")

    return _serialize_profile(profile)


def require_job_seeker_profile(
    current_user: User,
    db: Session,
) -> JobSeekerProfile:
    """Dependency helper that ensures the authenticated job_seeker has a profile.

    Raises HTTP 409 with a stable message when profile is missing. Consumers can Depend on this.
    """
    try:
        stmt = select(JobSeekerProfile).where(JobSeekerProfile.user_id == current_user.id)
        profile = db.execute(stmt).scalars().first()
    except Exception:
        logger.error("DB error loading job seeker profile for require hook for user %s", current_user.id, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")

    if profile is None:
        # Use 409 Conflict to indicate precondition (profile) required for the operation
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PROFILE_REQUIRED")

    return profile
