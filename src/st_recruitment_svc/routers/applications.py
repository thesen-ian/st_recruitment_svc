from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, model_validator, field_validator, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from st_recruitment_svc.auth import get_current_user, require_job_seeker, require_company
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
    Visibility,
    UserRole,
    ApplicationStatus,
    ApplicationStatusHistory,
)

logger = logging.getLogger(__name__)
router = APIRouter()

PAGE_SIZE = 20


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


# 5 MiB limit for application answer files
_MAX_FILE_ANSWER_BYTES = 5 * 1024 * 1024


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

    # Pre-validate file answers (existence, ownership, privacy, size)
    try:
        for a in answers_list:
            q = q_by_id[a.question_id]
            qtype = q.type

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
                # Must answer with file_object_id
                if a.file_object_id is None:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file answer required for file question")

                # load file object
                try:
                    stmt = select(FileObject).where(FileObject.id == a.file_object_id)
                    fo = db.execute(stmt).scalars().first()
                except Exception:
                    logger.exception("DB error loading file object %s", a.file_object_id)
                    raise HTTPException(status_code=500, detail="Internal error")

                if fo is None:
                    # follow existing convention: not found
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found")

                # Ownership enforcement
                if getattr(fo, "owner_user_id", None) != current_user.id:
                    # do not allow linking other users' files
                    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="file does not belong to authenticated user")

                # Visibility/privacy enforcement: require private
                vis = getattr(fo, "visibility", None)
                if vis is None:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file visibility unknown; cannot verify privacy")
                if vis != Visibility.private.value:
                    # We do not attempt to move or change files between public/private storage
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file must be private")

                # Size enforcement
                size = getattr(fo, "size_bytes", None)
                if size is None:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file size unknown; cannot verify limit")
                if size > _MAX_FILE_ANSWER_BYTES:
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="file exceeds maximum allowed size")
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported question type")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error validating answers for job %s user %s", job_id, getattr(current_user, "id", None))
        raise HTTPException(status_code=500, detail="Internal error")

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
                file_object_id=a.file_object_id if a.file_object_id is not None else None,
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


# ----------------- Listing endpoints -----------------
@router.get("/job-seekers/me/applications")
def list_my_applications(
    page: int = Query(1, ge=1),
    current_user = Depends(require_job_seeker()),
    db: Session = Depends(get_db),
) -> Any:
    try:
        offset = (page - 1) * PAGE_SIZE
        stmt = (
            select(Application)
            .where(Application.job_seeker_user_id == current_user.id)
            .order_by(Application.applied_at.desc(), Application.id.desc())
            .offset(offset)
            .limit(PAGE_SIZE)
        )
        res = db.execute(stmt)
        apps = res.scalars().all()

        items: List[Dict[str, Any]] = []
        for a in apps:
            items.append(
                {
                    "application_id": a.id,
                    "job_id": a.job_id,
                    "selected_resume_id": a.selected_resume_id,
                    "status": a.status,
                    "applied_at": a.applied_at.isoformat() if getattr(a, "applied_at", None) is not None else None,
                }
            )

        return {"items": items, "page": page, "page_size": PAGE_SIZE}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to list applications for job seeker %s", getattr(current_user, "id", None))
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/companies/me/applications")
def list_company_applications(
    job_id: Optional[str] = Query(None),
    app_status: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    current_user = Depends(require_company()),
    db: Session = Depends(get_db),
) -> Any:
    try:
        # If job_id provided, verify ownership first
        if job_id is not None:
            try:
                job_stmt = select(JobPosting).where(JobPosting.id == job_id)
                job = db.execute(job_stmt).scalars().first()
            except Exception:
                logger.exception("DB error loading job %s for ownership check", job_id)
                raise HTTPException(status_code=500, detail="Internal error")

            if job is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
            if job.company_id != current_user.id:
                # Consistent with other owner checks: 403 when resource exists but not owned
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not owner of job")

        # Base query: join to JobPosting to enforce tenant scoping
        stmt = select(Application).join(JobPosting, JobPosting.id == Application.job_id).where(JobPosting.company_id == current_user.id)

        if job_id is not None:
            stmt = stmt.where(Application.job_id == job_id)
        if app_status is not None:
            stmt = stmt.where(Application.status == app_status)

        stmt = stmt.order_by(Application.applied_at.desc(), Application.id.desc())

        offset = (page - 1) * PAGE_SIZE
        stmt = stmt.offset(offset).limit(PAGE_SIZE)

        res = db.execute(stmt)
        apps = res.scalars().all()

        items: List[Dict[str, Any]] = []
        for a in apps:
            items.append(
                {
                    "application_id": a.id,
                    "job_id": a.job_id,
                    "job_seeker_user_id": a.job_seeker_user_id,
                    "selected_resume_id": a.selected_resume_id,
                    "status": a.status,
                    "applied_at": a.applied_at.isoformat() if getattr(a, "applied_at", None) is not None else None,
                }
            )

        return {"items": items, "page": page, "page_size": PAGE_SIZE}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to list applications for company %s", getattr(current_user, "id", None))
        raise HTTPException(status_code=500, detail="Internal error")


# ----------------- Application detail endpoint -----------------
@router.get("/applications/{application_id}")
def get_application_detail(
    application_id: str,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """
    Return application details including answers. Authorization: job seeker owner, company owner, or admin.
    """
    try:
        stmt = select(Application).options(selectinload(Application.answers)).where(Application.id == application_id)
        app_row = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading application %s", application_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if app_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="application not found")

    # Load job posting to check company ownership and question ordering when available
    try:
        job_stmt = select(JobPosting).options(selectinload(JobPosting.questions)).where(JobPosting.id == app_row.job_id)
        job = db.execute(job_stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading job for application %s", application_id)
        raise HTTPException(status_code=500, detail="Internal error")

    # Authorization
    allowed = False
    try:
        if getattr(current_user, "role", None) == UserRole.admin:
            allowed = True
        elif getattr(current_user, "role", None) == UserRole.job_seeker and app_row.job_seeker_user_id == current_user.id:
            allowed = True
        elif getattr(current_user, "role", None) == UserRole.company:
            # require job existence and matching company id
            if job is not None and getattr(job, "company_id", None) == current_user.id:
                allowed = True
    except Exception:
        logger.exception("Error during authorization checks for application %s and user %s", application_id, getattr(current_user, "id", None))
        raise HTTPException(status_code=500, detail="Internal error")

    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    # Prepare question ordering map if job and questions available
    q_order: Dict[str, int] = {}
    if job is not None:
        try:
            qs = getattr(job, "questions", []) or []
            for idx, q in enumerate(qs):
                # Use explicit position if available otherwise fallback to enumeration
                pos = getattr(q, "position", None)
                q_order[q.id] = pos if pos is not None else idx
        except Exception:
            logger.debug("Failed to build question order map for job %s", getattr(job, "id", None))

    # Build answers list with stable ordering
    answers_out: List[Dict[str, Optional[str]]] = []
    answers = getattr(app_row, "answers", []) or []

    def answer_sort_key(ans: ApplicationAnswer):
        # sort by question position if known, else by question_id to be stable
        pos = q_order.get(ans.question_id)
        return (pos if pos is not None else 10**9, ans.question_id)

    try:
        for ans in sorted(answers, key=answer_sort_key):
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


# ----------------- Status transition endpoint -----------------
class StatusChangeRequest(BaseModel):
    to_status: str
    notes: Optional[str] = None
    # Optional interview scheduling details to validate when scheduling
    interview_at: Optional[datetime] = None
    interview_end: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_interview_times(cls, model: "StatusChangeRequest") -> "StatusChangeRequest":
        # If interview_end provided ensure interview_at exists and end > start
        if model.interview_end is not None and model.interview_at is None:
            raise ValueError("interview_at required when interview_end provided")
        if model.interview_at is not None and model.interview_at.tzinfo is None:
            # enforce timezone-aware datetimes
            raise ValueError("interview_at must be timezone-aware")
        if model.interview_end is not None and model.interview_end.tzinfo is None:
            raise ValueError("interview_end must be timezone-aware")
        if model.interview_at is not None and model.interview_end is not None:
            if model.interview_end <= model.interview_at:
                raise ValueError("interview_end must be after interview_at")
        return model


class StatusChangeResponse(BaseModel):
    application_id: str
    status: str
    updated_at: Optional[str] = None


@router.post("/applications/{application_id}/status", response_model=StatusChangeResponse)
def change_application_status(
    application_id: str,
    payload: StatusChangeRequest,
    current_user = Depends(require_company()),
    db: Session = Depends(get_db),
) -> Any:
    # Only company actors may call; require_company dependency enforces role
    try:
        stmt = select(Application).where(Application.id == application_id)
        app_row = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading application %s", application_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if app_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="application not found")

    # verify ownership via JobPosting
    try:
        job_stmt = select(JobPosting).where(JobPosting.id == app_row.job_id)
        job = db.execute(job_stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading job for application %s", application_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if job is None or getattr(job, "company_id", None) != current_user.id:
        # Either job not found or not owned by this company
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not owner of application")

    # Determine current and target statuses
    def _val(s):
        # normalize enum or string to string value
        try:
            import enum as _enum
            if isinstance(s, _enum.Enum):
                return s.value
        except Exception:
            pass
        return str(s) if s is not None else None

    current_status = _val(getattr(app_row, "status", None))
    to_status = payload.to_status

    # Define allowed linear flow
    flow = [
        ApplicationStatus.Applied.value,
        ApplicationStatus.ResumeReview.value,
        ApplicationStatus.PhoneScreen.value,
        ApplicationStatus.InterviewScheduled.value,
        ApplicationStatus.InterviewComplete.value,
        ApplicationStatus.FinalReview.value,
        ApplicationStatus.Offer.value,
        ApplicationStatus.Rejected.value,
    ]

    # Terminal protection
    if current_status in (ApplicationStatus.Offer.value, ApplicationStatus.Rejected.value):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot transition from terminal status")

    # Must be a known to_status
    if to_status not in flow:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown target status")

    # Validate forward-only single-step transitions
    try:
        curr_idx = flow.index(current_status)
    except ValueError:
        # if current status unknown, disallow
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="current status invalid")

    # Special case: Final Review can go to Offer or Rejected
    if current_status == ApplicationStatus.FinalReview.value:
        if to_status not in (ApplicationStatus.Offer.value, ApplicationStatus.Rejected.value):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid transition from Final Review")
    else:
        # normal expected next step
        expected_idx = curr_idx + 1
        if expected_idx >= len(flow) or flow[expected_idx] != to_status:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid transition; can only move to next stage")

    # Interview Scheduled specific validation
    if to_status == ApplicationStatus.InterviewScheduled.value:
        # require interview_at and it must be in the future
        if payload.interview_at is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="interview_at required when scheduling interview")
        now = datetime.now(tz=timezone.utc)
        if payload.interview_at <= now:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="interview must be scheduled in the future")
        # if interview_end provided validated by pydantic already

    # Persist status change and history atomically
    try:
        from_status_val = current_status
        app_row.status = to_status
        # update updated_at if DB doesn't auto-update (keep simple approach)
        try:
            app_row.updated_at = datetime.now(tz=timezone.utc)
        except Exception:
            pass

        hist = ApplicationStatusHistory(
            application_id=app_row.id,
            from_status=from_status_val,
            to_status=to_status,
            changed_by_user_id=current_user.id,
            notes=payload.notes,
        )
        db.add(hist)
        db.add(app_row)
        db.commit()
        db.refresh(app_row)
    except HTTPException:
        try:
            db.rollback()
        except Exception:
            logger.exception("rollback failed after HTTPException in status change")
        raise
    except Exception as e:
        logger.exception("Failed to change status for application %s: %s", application_id, e)
        try:
            db.rollback()
        except Exception:
            logger.exception("rollback failed after exception in status change")
        raise HTTPException(status_code=500, detail="failed to change status")

    resp = StatusChangeResponse(application_id=app_row.id, status=app_row.status, updated_at=getattr(app_row, "updated_at", None).isoformat() if getattr(app_row, "updated_at", None) is not None else None)
    return resp.model_dump() if hasattr(resp, "model_dump") else resp.dict()
