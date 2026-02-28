from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from st_recruitment_svc.models.base import Job, get_db
from . import companies

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/jobs/{job_id}")
def guard_public_job(job_id: str, db: Session = Depends(get_db)) -> Any:
    """Guard public job detail: hide legacy soft-removed jobs then delegate.

    This router is included before the companies router to enforce the admin
    is_removed flag on legacy Job rows without modifying the large companies
    module. If a legacy Job exists and is_removed is true, respond 404.
    Otherwise delegate to the existing JobPosting detail handler.
    """
    try:
        stmt = select(Job).where(Job.id == job_id)
        legacy = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error checking legacy Job %s", job_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if legacy is not None and bool(getattr(legacy, "is_removed", False)):
        # Treat removed jobs as not found to avoid information leakage
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")

    # Delegate to existing public job handler which loads JobPosting and validates state
    return companies.get_public_job(job_id, db)
