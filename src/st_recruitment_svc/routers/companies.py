from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, AnyUrl, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from st_recruitment_svc.auth import require_company
from st_recruitment_svc.models.base import (
    CompanyProfile,
    User,
    get_db,
    ensure_company_profile,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class CompanyProfileIn(BaseModel):
    # Accept business_registration_number in payload but treat as immutable.
    business_registration_number: Optional[str] = None
    description: Optional[str] = Field(default=None, max_length=10000)
    website_url: Optional[AnyUrl] = None
    industry: Optional[str] = Field(default=None, max_length=255)
    size: Optional[str] = Field(default=None, max_length=255)
    hq_location: Optional[str] = Field(default=None, max_length=255)


class CompanyProfileOut(CompanyProfileIn):
    # Override website_url to plain string to avoid Pydantic AnyUrl normalization
    # which can change formatting (e.g. append trailing slash). We validate input
    # with AnyUrl but return a stable string representation.
    website_url: Optional[str] = None

    id: str
    company_id: str
    company_name: Optional[str]
    business_registration_number: Optional[str]
    contact_information: Optional[Dict[str, Any]]
    logo_file_object_id: Optional[str]
    cover_file_object_id: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]


def _serialize(company_profile: CompanyProfile, company_user: User) -> Dict[str, Any]:
    return {
        "id": company_profile.id,
        "company_id": company_profile.company_id,
        "company_name": getattr(company_user, "company_name", None),
        "business_registration_number": getattr(company_user, "business_registration_number", None),
        "contact_information": getattr(company_user, "contact_information", None),
        "description": company_profile.description,
        "website_url": company_profile.website_url,
        "industry": company_profile.industry,
        "size": company_profile.size,
        "hq_location": company_profile.hq_location,
        "logo_file_object_id": company_profile.logo_file_object_id,
        "cover_file_object_id": company_profile.cover_file_object_id,
        "created_at": company_profile.created_at.isoformat() if hasattr(company_profile, "created_at") and company_profile.created_at is not None else None,
        "updated_at": company_profile.updated_at.isoformat() if hasattr(company_profile, "updated_at") and company_profile.updated_at is not None else None,
    }


@router.get("/companies/me", response_model=CompanyProfileOut)
def get_my_company(
    current_user: User = Depends(require_company()),
    db: Session = Depends(get_db),
) -> Any:
    """Return authenticated company's profile. Create profile when missing for legacy data."""
    try:
        stmt = select(CompanyProfile).where(CompanyProfile.company_id == current_user.id)
        profile = db.execute(stmt).scalars().first()
    except Exception:
        logger.error("DB error loading company profile for user %s", current_user.id, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")

    if profile is None:
        # Ensure profile exists for legacy company users; encapsulate commit handling
        try:
            profile = ensure_company_profile(db, current_user)
            # ensure_company_profile may not commit; commit here to persist changes
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to ensure company profile for user %s", current_user.id)
            raise HTTPException(status_code=500, detail="Internal error")
        # reload persisted profile
        try:
            stmt = select(CompanyProfile).where(CompanyProfile.company_id == current_user.id)
            profile = db.execute(stmt).scalars().first()
        except Exception:
            logger.exception("Failed to reload created company profile for user %s", current_user.id)
            raise HTTPException(status_code=500, detail="Internal error")

    if profile is None:
        # This should not happen but keep contract
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="profile not found")

    return _serialize(profile, current_user)


@router.put("/companies/me", response_model=CompanyProfileOut)
def upsert_my_company(
    payload: CompanyProfileIn,
    current_user: User = Depends(require_company()),
    db: Session = Depends(get_db),
) -> Any:
    """Update editable company profile fields. Immutable registration fields are not editable here."""
    try:
        stmt = select(CompanyProfile).where(CompanyProfile.company_id == current_user.id)
        profile = db.execute(stmt).scalars().first()
    except Exception:
        logger.error("DB error loading company profile for upsert for user %s", current_user.id, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")

    now = datetime.now(tz=timezone.utc)

    try:
        if profile is None:
            profile = ensure_company_profile(db, current_user)
            db.flush()

        # Update allowed profile fields only. Do not modify company registration fields.
        profile.description = payload.description
        # Cast AnyUrl to string for safe DB binding and normalize single trailing slash
        if payload.website_url is not None:
            url_str = str(payload.website_url)
            if url_str.endswith("/"):
                # remove single trailing slash only
                url_str = url_str[:-1]
            profile.website_url = url_str
        else:
            profile.website_url = None
        profile.industry = payload.industry
        profile.size = payload.size
        profile.hq_location = payload.hq_location

        # touch updated_at if present
        if hasattr(profile, "updated_at"):
            profile.updated_at = now

        db.commit()
        # reload
        db.refresh(profile)
    except HTTPException:
        raise
    except Exception:
        logger.error("Failed to upsert company profile for user %s", current_user.id, exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="failed to save profile")

    return _serialize(profile, current_user)
