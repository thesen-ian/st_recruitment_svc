from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from st_recruitment_svc.auth import require_job_seeker
from st_recruitment_svc.models.base import (
    get_db,
    Application,
    ApplicationStatus,
    ApplicationStatusHistory,
    ApplicationAnswer,
    JobPosting,
    UserRole,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/applications/{application_id}/withdraw")
def withdraw_application(
    application_id: str,
    current_user = Depends(require_job_seeker()),
    db: Session = Depends(get_db),
) -> Any:
    """Allow a job seeker to withdraw their own application when in allowed statuses.

    Behaviour mirrors company status transition patterns: transactional update + history row.
    """
    try:
        stmt = select(Application).where(Application.id == application_id)
        app_row = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading application %s", application_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if app_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="application not found")

    # Ownership: only owning job seeker may withdraw
    try:
        if getattr(current_user, "role", None) != UserRole.job_seeker:
            # require_job_seeker should normally enforce this, but double-check
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        if getattr(app_row, "job_seeker_user_id", None) != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not owner of application")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error during authorization checks for application %s and user %s", application_id, getattr(current_user, "id", None))
        raise HTTPException(status_code=500, detail="Internal error")

    def _val(s):
        try:
            import enum as _enum
            if isinstance(s, _enum.Enum):
                return s.value
        except Exception:
            pass
        return str(s) if s is not None else None

    current_status = _val(getattr(app_row, "status", None))

    # Allowed statuses from which a job seeker can withdraw
    allowed = {
        ApplicationStatus.Applied.value,
        ApplicationStatus.ResumeReview.value,
        ApplicationStatus.PhoneScreen.value,
    }

    if current_status not in allowed:
        # follow the service convention used by status endpoint
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot withdraw in current status")

    # Determine representation for withdrawal: no Withdrawn enum exists, so use Rejected
    to_status = ApplicationStatus.Rejected.value

    # Persist status change and history atomically
    try:
        from_status_val = current_status
        app_row.status = to_status
        try:
            app_row.updated_at = datetime.now(tz=timezone.utc)
        except Exception:
            pass

        hist = ApplicationStatusHistory(
            application_id=app_row.id,
            from_status=from_status_val,
            to_status=to_status,
            changed_by_user_id=current_user.id,
            notes="Withdrawn by job seeker",
        )
        db.add(hist)
        db.add(app_row)
        db.commit()
        db.refresh(app_row)
    except HTTPException:
        try:
            db.rollback()
        except Exception:
            logger.exception("rollback failed after HTTPException in withdraw")
        raise
    except Exception as e:
        logger.exception("Failed to withdraw application %s: %s", application_id, e)
        try:
            db.rollback()
        except Exception:
            logger.exception("rollback failed after exception in withdraw")
        raise HTTPException(status_code=500, detail="failed to withdraw application")

    # Return application detail using same shape as get_application_detail
    try:
        stmt = select(Application).options(selectinload(Application.answers)).where(Application.id == application_id)
        app_row = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error re-loading application %s", application_id)
        raise HTTPException(status_code=500, detail="Internal error")

    answers_out: List[Dict[str, Optional[str]]] = []
    answers = getattr(app_row, "answers", []) or []
    try:
        for ans in answers:
            answers_out.append(
                {
                    "question_id": ans.question_id,
                    "answer_text": ans.answer_text,
                    "selected_option_id": ans.selected_option_id,
                    "file_object_id": ans.file_object_id,
                }
            )
    except Exception:
        logger.exception("Failed to serialize answers for application %s", application_id)
        raise HTTPException(status_code=500, detail="Internal error")

    result = {
        "application_id": app_row.id,
        "job_id": app_row.job_id,
        "job_seeker_user_id": app_row.job_seeker_user_id,
        "selected_resume_id": app_row.selected_resume_id,
        "cover_letter": app_row.cover_letter,
        "status": app_row.status,
        "applied_at": app_row.applied_at.isoformat() if getattr(app_row, "applied_at", None) is not None else None,
        "answers": answers_out,
    }
    return result
