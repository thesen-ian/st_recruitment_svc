from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, func, text, or_
from sqlalchemy.orm import Session

from st_recruitment_svc.models.base import (
    JobPosting,
    JobPostingState,
    get_db,
    engine,
)

logger = logging.getLogger(__name__)
router = APIRouter()

PAGE_SIZE = 20


class JobSearchItemOut(BaseModel):
    id: str
    company_id: str
    company_name: Optional[str]
    title: str
    location: Optional[str]
    employment_type: Optional[str]
    seniority: Optional[str]
    salary_min: Optional[int]
    salary_max: Optional[int]
    currency: Optional[str]
    remote_policy: Optional[str]
    created_at: Optional[str]


class JobSearchResults(BaseModel):
    items: List[JobSearchItemOut]
    page: int
    page_size: int


def _serialize_job(job: JobPosting) -> Dict[str, Any]:
    return {
        "id": job.id,
        "company_id": job.company_id,
        "company_name": getattr(job, "company_name", None),
        "title": job.title,
        "location": job.location,
        "employment_type": job.employment_type,
        "seniority": job.seniority,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "currency": job.currency,
        "remote_policy": job.remote_policy,
        "created_at": job.created_at.isoformat() if getattr(job, "created_at", None) is not None else None,
    }


def _parse_bool_like(val: Optional[str]) -> Optional[bool]:
    if val is None:
        return None
    v = str(val).strip().lower()
    if v in ("1", "true", "t", "yes", "y"):
        return True
    if v in ("0", "false", "f", "no", "n"):
        return False
    return None


def _build_search_statement(
    q: Optional[str] = None,
    location: Optional[str] = None,
    job_type: Optional[str] = None,
    salary_min: Optional[int] = None,
    salary_max: Optional[int] = None,
    skills_list: Optional[List[str]] = None,
    experience: Optional[str] = None,
    industry: Optional[str] = None,
    company_size: Optional[str] = None,
    remote_bool: Optional[bool] = None,
    page: int = 1,
    dialect_name: Optional[str] = None,
) -> Tuple[Any, Optional[Any], bool]:
    """Build SQLAlchemy select statement for job search and indicate if FTS is used.

    Returns (stmt, rank_expr, use_fts). rank_expr may be None when not using FTS.
    This helper is separated so tests can compile the same statement the endpoint uses.
    """
    stmt = select(JobPosting)
    where_clauses = [JobPosting.state == JobPostingState.active]

    if location:
        where_clauses.append(JobPosting.location == location)
    if job_type:
        where_clauses.append(JobPosting.employment_type == job_type)

    # Salary overlap semantics. Use or_ for SQLAlchemy ORs.
    if salary_min is not None and salary_max is not None:
        where_clauses.append(
            and_(
                or_(JobPosting.salary_max.is_(None), JobPosting.salary_max >= salary_min),
                or_(JobPosting.salary_min.is_(None), JobPosting.salary_min <= salary_max),
            )
        )
    else:
        if salary_min is not None:
            where_clauses.append(or_(JobPosting.salary_max.is_(None), JobPosting.salary_max >= salary_min))
        if salary_max is not None:
            where_clauses.append(or_(JobPosting.salary_min.is_(None), JobPosting.salary_min <= salary_max))

    if experience:
        # map experience to seniority field on JobPosting (exact match)
        where_clauses.append(JobPosting.seniority == experience)

    # Remote parsing: treat remote_policy containing 'remote' (case-insensitive) as remote
    if remote_bool is True:
        where_clauses.append(JobPosting.remote_policy.isnot(None))
        where_clauses.append(func.lower(JobPosting.remote_policy).like("%remote%"))
    elif remote_bool is False:
        # Only include jobs where remote_policy is empty/does not indicate remote
        where_clauses.append(or_(JobPosting.remote_policy.is_(None), ~func.lower(JobPosting.remote_policy).like("%remote%")))

    # Skills: only apply if schema exposes a skills attribute on JobPosting.
    # Do explicit attribute checks rather than catching broad exceptions.
    if skills_list:
        if hasattr(JobPosting, "skills"):
            # Try an attribute-level check for array "contains" support
            has_contains = hasattr(JobPosting.skills, "contains")
            for skill in skills_list:
                if has_contains:
                    where_clauses.append(JobPosting.skills.contains([skill]))
                else:
                    # Fallback to textual containment if a skills column exists but not array typed
                    where_clauses.append(func.lower(JobPosting.skills).like(f"%{skill.lower()}%"))
        else:
            # No skills column in schema; intentionally ignore per spec.
            pass

    # industry and company_size: try to filter on JobPosting if present; otherwise ignore.
    if industry:
        if hasattr(JobPosting, "industry"):
            where_clauses.append(JobPosting.industry == industry)
        else:
            # If industry lives on related CompanyProfile, we intentionally skip join here.
            # Per spec: do not error when attribute not present.
            pass

    if company_size:
        if hasattr(JobPosting, "company_size"):
            where_clauses.append(JobPosting.company_size == company_size)
        else:
            # Same as industry: ignore when not present on JobPosting
            pass

    # Determine dialect for FTS decision
    dialect = dialect_name or engine.dialect.name
    rank_expr = None
    use_fts = False
    if q and dialect == "postgresql":
        use_fts = True
        # Prefer websearch_to_tsquery for user-friendly queries. Use SQLAlchemy func to let it bind.
        tsquery = func.websearch_to_tsquery("english", q)
        # Use SQLAlchemy's op to express the @@ operator against the search_document column.
        where_clauses.append(JobPosting.search_document.op("@@")(tsquery))
        rank_expr = func.ts_rank_cd(JobPosting.search_document, tsquery)
        # include rank as additional selected column for ordering and potential inspection
        stmt = stmt.add_columns(rank_expr.label("rank"))
    elif q:
        # SQLite (or other) fallback: ilike across title/description/company_name
        q_like = f"%{q}%"
        or_conditions = [JobPosting.title.ilike(q_like), JobPosting.description.ilike(q_like)]
        # Include company_name only if present to avoid duplicating title checks
        if hasattr(JobPosting, "company_name"):
            or_conditions.append(JobPosting.company_name.ilike(q_like))
        where_clauses.append(or_(*or_conditions))

    if where_clauses:
        stmt = stmt.where(and_(*where_clauses))

    # Ordering: when FTS used, order by rank desc then recency, else recency
    if q and use_fts and rank_expr is not None:
        stmt = stmt.order_by(rank_expr.desc(), JobPosting.created_at.desc(), JobPosting.id.desc())
    else:
        stmt = stmt.order_by(JobPosting.created_at.desc(), JobPosting.id.desc())

    # Pagination
    offset = (page - 1) * PAGE_SIZE
    stmt = stmt.offset(offset).limit(PAGE_SIZE)

    return stmt, rank_expr, use_fts


@router.get("/search/jobs", response_model=JobSearchResults)
def search_jobs(
    q: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    job_type: Optional[str] = Query(None),
    salary_min: Optional[int] = Query(None, ge=0),
    salary_max: Optional[int] = Query(None, ge=0),
    skills: Optional[str] = Query(None),
    experience: Optional[str] = Query(None),
    industry: Optional[str] = Query(None),
    company_size: Optional[str] = Query(None),
    remote: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
) -> Any:
    try:
        # Validate salary range
        if salary_min is not None and salary_max is not None and salary_min > salary_max:
            raise HTTPException(status_code=422, detail="salary_min cannot be greater than salary_max")

        # parse skills into list
        skills_list: Optional[List[str]] = None
        if skills:
            skills_list = [s.strip() for s in skills.split(",") if s and s.strip()]
            if len(skills_list) == 0:
                skills_list = None

        # parse remote
        remote_bool = _parse_bool_like(remote)

        dialect_name = engine.dialect.name

        # Validate experience against enum values if the column exposes them. Raise 422 on invalid input.
        if experience and hasattr(JobPosting, "seniority"):
            try:
                col = JobPosting.__table__.columns.get("seniority")
                col_type = getattr(col, "type", None)
                enum_vals = getattr(col_type, "enums", None)
                if enum_vals is not None and experience not in enum_vals:
                    raise HTTPException(status_code=422, detail="invalid experience value")
            except HTTPException:
                raise
            except Exception:
                # If introspection fails, don't block search; accept the value.
                pass

        stmt, rank_expr, use_fts = _build_search_statement(
            q=q,
            location=location,
            job_type=job_type,
            salary_min=salary_min,
            salary_max=salary_max,
            skills_list=skills_list,
            experience=experience,
            industry=industry,
            company_size=company_size,
            remote_bool=remote_bool,
            page=page,
            dialect_name=dialect_name,
        )

        # execute
        res = db.execute(stmt)
        rows = res.fetchall()
        items: List[JobPosting] = []
        for r in rows:
            # SQLAlchemy returns Row objects; the first element is the mapped instance when present.
            try:
                candidate = r[0]
            except Exception:
                candidate = r
            items.append(candidate)

        serialized = [_serialize_job(j) for j in items]
        return {"items": serialized, "page": page, "page_size": PAGE_SIZE}
    except HTTPException:
        raise
    except Exception:
        logger.error("Search jobs failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")
