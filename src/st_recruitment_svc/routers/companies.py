from __future__ import annotations

import logging
from typing import Any, Dict, Optional, List
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Body
from pydantic import BaseModel, AnyUrl, Field, validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from st_recruitment_svc.auth import require_company, get_current_user
from st_recruitment_svc.models.base import (
    CompanyProfile,
    User,
    get_db,
    ensure_company_profile,
    Job,
    JobStatus,
    UserRole,
    FilePurpose,
    JobPosting,
    JobPostingQuestion,
    JobPostingQuestionOption,
    JobPostingState,
    QuestionType,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class CompanyProfileIn(BaseModel):
    business_registration_number: Optional[str] = None
    description: Optional[str] = Field(default=None, max_length=10000)
    website_url: Optional[AnyUrl] = None
    industry: Optional[str] = Field(default=None, max_length=255)
    size: Optional[str] = Field(default=None, max_length=255)
    hq_location: Optional[str] = Field(default=None, max_length=255)


class CompanyProfileOut(CompanyProfileIn):
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


class JobSummary(BaseModel):
    id: str
    title: str
    created_at: Optional[str]


class CompanyPublicOut(BaseModel):
    id: str
    company_id: str
    company_name: Optional[str]
    description: Optional[str]
    website_url: Optional[str]
    industry: Optional[str]
    size: Optional[str]
    hq_location: Optional[str]
    logo_file_object_id: Optional[str]
    cover_file_object_id: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    active_job_postings: List[JobSummary] = []


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
    try:
        stmt = select(CompanyProfile).where(CompanyProfile.company_id == current_user.id)
        profile = db.execute(stmt).scalars().first()
    except Exception:
        logger.error("DB error loading company profile for user %s", current_user.id, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")

    if profile is None:
        try:
            profile = ensure_company_profile(db, current_user)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to ensure company profile for user %s", current_user.id)
            raise HTTPException(status_code=500, detail="Internal error")
        try:
            stmt = select(CompanyProfile).where(CompanyProfile.company_id == current_user.id)
            profile = db.execute(stmt).scalars().first()
        except Exception:
            logger.exception("Failed to reload created company profile for user %s", current_user.id)
            raise HTTPException(status_code=500, detail="Internal error")

    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="profile not found")

    return _serialize(profile, current_user)


@router.put("/companies/me", response_model=CompanyProfileOut)
def upsert_my_company(
    payload: CompanyProfileIn,
    current_user: User = Depends(require_company()),
    db: Session = Depends(get_db),
) -> Any:
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

        profile.description = payload.description
        if payload.website_url is not None:
            url_str = str(payload.website_url)
            if url_str.endswith("/"):
                url_str = url_str[:-1]
            profile.website_url = url_str
        else:
            profile.website_url = None
        profile.industry = payload.industry
        profile.size = payload.size
        profile.hq_location = payload.hq_location

        if hasattr(profile, "updated_at"):
            profile.updated_at = now

        db.commit()
        db.refresh(profile)
    except HTTPException:
        raise
    except Exception:
        logger.error("Failed to upsert company profile for user %s", current_user.id, exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="failed to save profile")

    return _serialize(profile, current_user)


@router.get("/companies/{company_id}", response_model=CompanyPublicOut)
def get_company_public(
    company_id: str,
    db: Session = Depends(get_db),
) -> Any:
    try:
        stmt = select(User).where(User.id == company_id)
        company_user = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading user %s", company_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if company_user is None or company_user.role != UserRole.company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="company not found")

    try:
        stmt = select(CompanyProfile).where(CompanyProfile.company_id == company_user.id)
        profile = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading company profile for user %s", company_user.id)
        raise HTTPException(status_code=500, detail="Internal error")

    if profile is None:
        try:
            profile = ensure_company_profile(db, company_user)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to ensure company profile for user %s", company_user.id)
            raise HTTPException(status_code=500, detail="Internal error")
        try:
            stmt = select(CompanyProfile).where(CompanyProfile.company_id == company_user.id)
            profile = db.execute(stmt).scalars().first()
        except Exception:
            logger.exception("Failed to reload created company profile for user %s", company_user.id)
            raise HTTPException(status_code=500, detail="Internal error")

    try:
        jobs_stmt = select(Job).where(Job.company_id == company_user.id, Job.status == JobStatus.published).order_by(Job.created_at.desc())
        jobs = db.execute(jobs_stmt).scalars().all()
    except Exception:
        logger.exception("DB error loading jobs for company %s", company_user.id)
        raise HTTPException(status_code=500, detail="Internal error")

    job_summaries = []
    for j in jobs:
        job_summaries.append(JobSummary(id=j.id, title=j.title, created_at=j.created_at.isoformat() if getattr(j, 'created_at', None) is not None else None))

    out = {
        "id": profile.id,
        "company_id": profile.company_id,
        "company_name": getattr(company_user, "company_name", None),
        "description": profile.description,
        "website_url": profile.website_url,
        "industry": profile.industry,
        "size": profile.size,
        "hq_location": profile.hq_location,
        "logo_file_object_id": profile.logo_file_object_id,
        "cover_file_object_id": profile.cover_file_object_id,
        "created_at": profile.created_at.isoformat() if getattr(profile, 'created_at', None) is not None else None,
        "updated_at": profile.updated_at.isoformat() if getattr(profile, 'updated_at', None) is not None else None,
        "active_job_postings": job_summaries,
    }

    return out


@router.post("/companies/me/logo")
def post_company_logo(
    upload_file: Optional[UploadFile] = File(None),
    current_user: User = Depends(require_company()),
    db: Session = Depends(get_db),
) -> Any:
    if upload_file is None:
        return {"company_id": current_user.id, "message": "logo endpoint stub"}

    try:
        from st_recruitment_svc.storage import persist_uploadfile_as_public, FileValidationError

        try:
            file_obj, public_url = persist_uploadfile_as_public(db, current_user.id, upload_file, FilePurpose.company_logo)
        except FileValidationError as e:
            msg = str(e)
            if "exceeds maximum size" in msg or "file exceeds maximum size" in msg:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=msg)
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=msg)

        try:
            stmt = select(CompanyProfile).where(CompanyProfile.company_id == current_user.id)
            profile = db.execute(stmt).scalars().first()
        except Exception:
            logger.exception("DB error loading company profile for user %s", current_user.id)
            raise HTTPException(status_code=500, detail="Internal error")

        if profile is None:
            profile = ensure_company_profile(db, current_user)
            db.flush()

        profile.logo_file_object_id = file_obj.id

        db.commit()
        db.refresh(profile)

        return {"company_id": current_user.id, "logo_file_object_id": profile.logo_file_object_id, "public_url": public_url}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to upload company logo for user %s", current_user.id)
        try:
            db.rollback()
        except Exception:
            logger.exception("Failed to rollback DB after logo upload failure for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="failed to upload logo")


@router.post("/companies/me/cover")
def post_company_cover(
    upload_file: UploadFile = File(...),
    current_user: User = Depends(require_company()),
    db: Session = Depends(get_db),
) -> Any:
    try:
        from st_recruitment_svc.storage import persist_uploadfile_as_public, FileValidationError

        try:
            file_obj, public_url = persist_uploadfile_as_public(db, current_user.id, upload_file, FilePurpose.company_logo)
        except FileValidationError as e:
            msg = str(e)
            if "exceeds maximum size" in msg or "file exceeds maximum size" in msg:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=msg)
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=msg)

        try:
            stmt = select(CompanyProfile).where(CompanyProfile.company_id == current_user.id)
            profile = db.execute(stmt).scalars().first()
        except Exception:
            logger.exception("DB error loading company profile for user %s", current_user.id)
            raise HTTPException(status_code=500, detail="Internal error")

        if profile is None:
            profile = ensure_company_profile(db, current_user)
            db.flush()

        profile.cover_file_object_id = file_obj.id

        db.commit()
        db.refresh(profile)

        return {"company_id": current_user.id, "cover_file_object_id": profile.cover_file_object_id, "public_url": public_url}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to upload company cover for user %s", current_user.id)
        try:
            db.rollback()
        except Exception:
            logger.exception("Failed to rollback DB after cover upload failure for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="failed to upload cover")


# ------------------- Job Posting endpoints -------------------

class OptionIn(BaseModel):
    label: str
    position: Optional[int] = None

    @validator("label")
    def label_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("option label must be non-empty")
        return v.strip()


class QuestionIn(BaseModel):
    type: QuestionType
    prompt: str
    is_required: Optional[bool] = False
    position: Optional[int] = None
    options: Optional[List[OptionIn]] = None

    @validator("prompt")
    def prompt_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("prompt must be non-empty")
        return v.strip()

    @validator("options", always=True)
    def validate_options_for_type(cls, v, values):
        qtype = values.get("type")
        # For strict behaviour: non-mcq must NOT have options
        if qtype != QuestionType.mcq and v:
            raise ValueError("options provided for non-mcq question")
        if qtype == QuestionType.mcq:
            if not v or len(v) < 1:
                raise ValueError("mcq questions require at least one option")
        return v


class JobPostingIn(BaseModel):
    title: str
    description: str
    location: Optional[str] = None
    employment_type: Optional[str] = None
    seniority: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    currency: Optional[str] = None
    remote_policy: Optional[str] = None
    deadline: Optional[datetime] = None
    questions: Optional[List[QuestionIn]] = None


class OptionOut(BaseModel):
    id: str
    label: str
    position: int


class QuestionOut(BaseModel):
    id: str
    type: QuestionType
    prompt: str
    is_required: bool
    position: int
    options: List[OptionOut] = []


class JobPostingOut(BaseModel):
    id: str
    company_id: str
    state: JobPostingState
    title: str
    description: str
    location: Optional[str]
    employment_type: Optional[str]
    seniority: Optional[str]
    salary_min: Optional[int]
    salary_max: Optional[int]
    currency: Optional[str]
    remote_policy: Optional[str]
    deadline: Optional[datetime]
    created_at: Optional[str]
    updated_at: Optional[str]
    published_at: Optional[str]
    closed_at: Optional[str]
    questions: List[QuestionOut] = []


def _serialize_job(job: JobPosting) -> Dict[str, Any]:
    questions_out: List[Dict[str, Any]] = []
    for q in sorted(getattr(job, "questions", []) or [], key=lambda x: x.position):
        options_out = [
            {"id": o.id, "label": o.label, "position": o.position}
            for o in sorted(getattr(q, "options", []) or [], key=lambda x: x.position)
        ]
        questions_out.append({
            "id": q.id,
            "type": q.type,
            "prompt": q.prompt,
            "is_required": bool(q.is_required),
            "position": q.position,
            "options": options_out,
        })

    return {
        "id": job.id,
        "company_id": job.company_id,
        "state": job.state,
        "title": job.title,
        "description": job.description,
        "location": job.location,
        "employment_type": job.employment_type,
        "seniority": job.seniority,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "currency": job.currency,
        "remote_policy": job.remote_policy,
        "deadline": job.deadline.isoformat() if getattr(job, "deadline", None) is not None else None,
        "created_at": job.created_at.isoformat() if getattr(job, "created_at", None) is not None else None,
        "updated_at": job.updated_at.isoformat() if getattr(job, "updated_at", None) is not None else None,
        "published_at": job.published_at.isoformat() if getattr(job, "published_at", None) is not None else None,
        "closed_at": job.closed_at.isoformat() if getattr(job, "closed_at", None) is not None else None,
        "questions": questions_out,
    }


@router.post("/jobs", response_model=JobPostingOut, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: JobPostingIn,
    current_user: User = Depends(require_company()),
    db: Session = Depends(get_db),
) -> Any:
    # Company derived from authenticated user
    try:
        job = JobPosting(
            company_id=current_user.id,
            state=JobPostingState.draft,
            title=payload.title,
            description=payload.description,
            location=payload.location,
            employment_type=payload.employment_type,
            seniority=payload.seniority,
            salary_min=payload.salary_min,
            salary_max=payload.salary_max,
            currency=payload.currency,
            remote_policy=payload.remote_policy,
            deadline=payload.deadline,
        )

        # Questions handling: assign positions if omitted
        questions = payload.questions or []
        for qi, q in enumerate(questions):
            pos = q.position if q.position is not None else qi
            question = JobPostingQuestion(type=q.type, prompt=q.prompt, is_required=bool(q.is_required), position=pos)
            # Options for mcq
            if q.type == QuestionType.mcq:
                opts = q.options or []
                for oi, o in enumerate(opts):
                    opt_pos = o.position if o.position is not None else oi
                    option = JobPostingQuestionOption(label=o.label, position=opt_pos)
                    question.options.append(option)
            job.questions.append(question)

        db.add(job)
        db.commit()
        db.refresh(job)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to create job posting", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="failed to create job")

    return _serialize_job(job)


@router.put("/jobs/{job_id}", response_model=JobPostingOut)
def update_job(
    job_id: str,
    payload: JobPostingIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    try:
        stmt = select(JobPosting).where(JobPosting.id == job_id)
        job = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading job %s", job_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    # Authorization: owner or admin
    if not (current_user.role == UserRole.admin or job.company_id == current_user.id):
        raise HTTPException(status_code=403, detail="not owner")

    # Disallow updates to closed jobs
    if job.state == JobPostingState.closed or (hasattr(job, "state") and job.state == JobPostingState.closed.value):
        raise HTTPException(status_code=422, detail="cannot modify closed job")

    try:
        # Replace semantics for questions: delete existing and recreate
        # Touch updated_at
        now = datetime.now(tz=timezone.utc)
        job.title = payload.title
        job.description = payload.description
        job.location = payload.location
        job.employment_type = payload.employment_type
        job.seniority = payload.seniority
        job.salary_min = payload.salary_min
        job.salary_max = payload.salary_max
        job.currency = payload.currency
        job.remote_policy = payload.remote_policy
        job.deadline = payload.deadline
        if hasattr(job, "updated_at"):
            job.updated_at = now

        # Delete existing questions via ORM relationship
        # Clearing list will remove orphans because of relationship cascade
        job.questions = []
        db.flush()

        questions = payload.questions or []
        for qi, q in enumerate(questions):
            pos = q.position if q.position is not None else qi
            question = JobPostingQuestion(type=q.type, prompt=q.prompt, is_required=bool(q.is_required), position=pos)
            if q.type == QuestionType.mcq:
                opts = q.options or []
                for oi, o in enumerate(opts):
                    opt_pos = o.position if o.position is not None else oi
                    option = JobPostingQuestionOption(label=o.label, position=opt_pos)
                    question.options.append(option)
            job.questions.append(question)

        db.commit()
        db.refresh(job)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to update job %s", job_id)
        db.rollback()
        raise HTTPException(status_code=500, detail="failed to update job")

    return _serialize_job(job)


@router.delete("/jobs/{job_id}")
def delete_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    try:
        stmt = select(JobPosting).where(JobPosting.id == job_id)
        job = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading job %s", job_id)
        raise HTTPException(status_code=500, detail="Internal error")

    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    if not (current_user.role == UserRole.admin or job.company_id == current_user.id):
        raise HTTPException(status_code=403, detail="not owner")

    try:
        db.delete(job)
        db.commit()
    except Exception:
        logger.exception("Failed to delete job %s", job_id)
        db.rollback()
        raise HTTPException(status_code=500, detail="failed to delete job")

    return {"deleted": True}
