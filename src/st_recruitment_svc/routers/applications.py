from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, model_validator, field_validator, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from st_recruitment_svc.auth import get_current_user, require_job_seeker
from st_recruitment_svc.models.base import (
    get_db,
    JobPosting,
    JobPostingQuestion,
    JobPostingQuestionOption,
    JobPostingState,
    QuestionType,
    Resume,
    FileObject,
    Application,
    ApplicationAnswer,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class AnswerIn(BaseModel):
    question_id: str
    answer_text: Optional[str] = None
    selected_option_id: Optional[str] = None
    file_object_id: Optional[str] = None

    @field_validator("answer_text")
    def strip_text(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v2 = v.strip()
        if v2 == "":
            raise ValueError("answer_text must be non-empty when provided")
        return v2

    @model_validator(mode="after")
    def exactly_one_answer_field(cls, model: "AnswerIn") -> "AnswerIn":
        at = 1 if model.answer_text is not None else 0
        so = 1 if model.selected_option_id is not None else 0
        fo = 1 if model.file_object_id is not None else 0
        total = at + so + fo
        if total != 1:
            raise ValueError("exactly one of answer_text, selected_option_id, file_object_id must be provided")
        return model


class ApplyRequest(BaseModel):
    selected_resume_id: str
    cover_letter: Optional[str] = None
    answers: List[AnswerIn] = Field(default_factory=list)


class ApplyResponse(BaseModel):
    application_id: str
    status: str
    applied_at: str


@router.post("/jobs/{job_id}/apply", response_model=ApplyResponse, status_code=status.HTTP_201_CREATED)
def apply_to_job(
    job_id: str,
    payload: ApplyRequest,
    current_user = Depends(require_job_seeker()),
    db: Session = Depends(get_db),
) -> Any:
    # Enforce verified user
    try:
        if not getattr(current_user, "email_verified", False):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="email not verified")

        # Load job posting with questions and options
        stmt = (
            select(JobPosting)
            .options(selectinload(JobPosting.questions).selectinload(JobPostingQuestion.options))
            .where(JobPosting.id == job_id)
        )
        job = db.execute(stmt).scalars().first()
    except HTTPException:
        raise
    except Exception:
        logger.exception("DB error loading job %s", job_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if job is None or job.state != JobPostingState.active:
        # do not leak existence of non-active jobs
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")

    # Validate resume ownership
    try:
        stmt = select(Resume).where(Resume.id == payload.selected_resume_id)
        resume = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading resume %s", payload.selected_resume_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="resume not found")
    if resume.user_id != current_user.id:
        # Do not allow using another user's resume
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="resume does not belong to the authenticated user")

    # Build question lookup
    job_questions: List[JobPostingQuestion] = getattr(job, "questions", []) or []
    q_by_id: Dict[str, JobPostingQuestion] = {q.id: q for q in job_questions}

    answers_list = payload.answers or []

    # Detect duplicate question_ids
    seen_qids = set()
    for a in answers_list:
        if a.question_id in seen_qids:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="duplicate question answers provided")
        seen_qids.add(a.question_id)

    # Ensure answers correspond to job questions
    for a in answers_list:
        if a.question_id not in q_by_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="answer for foreign question provided")

    # Ensure required questions are answered
    for q in job_questions:
        if q.is_required:
            if q.id not in seen_qids:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing required question answers")

    # Per-question validation
    for a in answers_list:
        q = q_by_id[a.question_id]
        qtype = q.type
        # Map domain: QuestionType.mcq corresponds to single-select
        if a.file_object_id is not None:
            # File answers not supported in this subtask
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File answers not supported yet")

        if qtype == QuestionType.text:
            if a.answer_text is None or (isinstance(a.answer_text, str) and not a.answer_text.strip()):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="text answer required for text question")
        elif qtype == QuestionType.mcq:
            if a.selected_option_id is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="option selection required for single-select question")
            # validate option belongs to question - normalize ids to strings for robust comparison
            opt_ids = {str(o.id) for o in getattr(q, "options", []) or []}
            if a.selected_option_id not in opt_ids:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="selected option does not belong to question")
        elif qtype == QuestionType.file:
            # File question exists but answering with files not supported
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File answers not supported yet")
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported question type")

    # Persist Application and ApplicationAnswer atomically
    try:
        app_row = Application(
            job_id=job.id,
            job_seeker_user_id=current_user.id,
            selected_resume_id=payload.selected_resume_id,
            cover_letter=payload.cover_letter,
        )
        db.add(app_row)
        db.flush()  # assign id

        # create answers
        for a in answers_list:
            ans = ApplicationAnswer(
                application_id=app_row.id,
                question_id=a.question_id,
                answer_text=a.answer_text if a.answer_text is not None else None,
                selected_option_id=a.selected_option_id if a.selected_option_id is not None else None,
                file_object_id=None,
            )
            db.add(ans)

        db.commit()
        db.refresh(app_row)
    except IntegrityError:
        try:
            db.rollback()
        except Exception:
            logger.exception("rollback failed after IntegrityError")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already applied")
    except HTTPException:
        try:
            db.rollback()
        except Exception:
            logger.exception("rollback failed after HTTPException")
        raise
    except Exception:
        logger.exception("Failed to persist application for job %s user %s", job_id, getattr(current_user, "id", None))
        try:
            db.rollback()
        except Exception:
            logger.exception("rollback failed after persistent error")
        raise HTTPException(status_code=500, detail="failed to create application")

    applied_at = app_row.applied_at.isoformat() if getattr(app_row, "applied_at", None) is not None else datetime.now(tz=timezone.utc).isoformat()
    resp = ApplyResponse(application_id=app_row.id, status=app_row.status, applied_at=applied_at)
    return resp.model_dump() if hasattr(resp, "model_dump") else resp.dict()
