from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Path, Body
from pydantic import BaseModel
from sqlalchemy import select, func, and_, or_

from st_recruitment_svc.auth import require_admin, get_current_user
from st_recruitment_svc.models.base import get_db, User, UserRole, UserStatus, Job, JobStatus, Application
from st_recruitment_svc.services.admin_audit import write_admin_audit

# Apply admin-only RBAC at router level so all /admin/* endpoints are protected
router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin())])


@router.get("/ping")
async def admin_ping() -> dict:
    """Minimal admin-only endpoint used for access-control tests.

    Real admin endpoints will live under this router in future subtasks.
    """
    return {"status": "ok"}


class RecentActivitySummary(BaseModel):
    new_users_last_n_days: Optional[int] = None
    new_jobs_last_n_days: Optional[int] = None
    new_applications_last_n_days: Optional[int] = None


@router.get("/metrics")
def admin_metrics(
    window_days: int = Query(7, ge=1),
    current_admin=Depends(get_current_user),
    db=Depends(get_db),
) -> Dict[str, Any]:
    """Return basic admin metrics and write audit log.

    Permissions: admin-only enforced at router level.
    """
    try:
        # total users by role
        stmt_roles = select(User.role, func.count()).group_by(User.role)
        rows = db.execute(stmt_roles).all()
        total_users_by_role = {r[0].value if hasattr(r[0], "value") else r[0]: r[1] for r in rows}

        # active jobs: exclude removed and require published status when possible
        try:
            stmt_jobs = select(func.count()).select_from(Job).where(
                and_(Job.is_removed.is_(False), Job.status == JobStatus.published)
            )
            active_jobs = db.execute(stmt_jobs).scalar_one()
        except Exception as e:
            logging.error(e, exc_info=True)
            # Fallback: count jobs where is_removed is false
            try:
                stmt_jobs = select(func.count()).select_from(Job).where(Job.is_removed.is_(False))
                active_jobs = db.execute(stmt_jobs).scalar_one()
            except Exception as ee:
                logging.error(ee, exc_info=True)
                active_jobs = 0

        # total applications
        try:
            stmt_apps = select(func.count()).select_from(Application)
            total_applications = db.execute(stmt_apps).scalar_one()
        except Exception as e:
            logging.error(e, exc_info=True)
            total_applications = 0

        # recent activity for window_days
        recent: Dict[str, Optional[int]] = {"new_users_last_n_days": None, "new_jobs_last_n_days": None, "new_applications_last_n_days": None}
        window_start = datetime.now(tz=timezone.utc) - timedelta(days=window_days)
        # Users
        try:
            stmt_new_users = select(func.count()).select_from(User).where(User.created_at >= window_start)
            recent["new_users_last_n_days"] = db.execute(stmt_new_users).scalar_one()
        except Exception as e:
            logging.error(e, exc_info=True)
            recent["new_users_last_n_days"] = None
        # Jobs
        try:
            stmt_new_jobs = select(func.count()).select_from(Job).where(Job.created_at >= window_start)
            recent["new_jobs_last_n_days"] = db.execute(stmt_new_jobs).scalar_one()
        except Exception as e:
            logging.error(e, exc_info=True)
            recent["new_jobs_last_n_days"] = None
        # Applications (applied_at)
        try:
            stmt_new_apps = select(func.count()).select_from(Application).where(Application.applied_at >= window_start)
            recent["new_applications_last_n_days"] = db.execute(stmt_new_apps).scalar_one()
        except Exception as e:
            logging.error(e, exc_info=True)
            recent["new_applications_last_n_days"] = None

        payload = {
            "total_users_by_role": total_users_by_role,
            "active_jobs": int(active_jobs or 0),
            "total_applications": int(total_applications or 0),
            "recent_activity_summary": recent,
        }

        # Audit
        write_admin_audit(
            db,
            admin_user_id=current_admin.id,
            action_type="METRICS_VIEW",
            target_type="system",
            target_id=None,
            details_json={"window_days": window_days},
        )
        db.commit()
        return payload
    except HTTPException:
        raise
    except Exception as e:
        logging.error(e, exc_info=True)
        # Defensive logging is handled by surrounding infrastructure; translate to 500
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="failed to compute metrics")


class UsersListResponseItem(BaseModel):
    id: str
    email: Optional[str]
    role: str
    status: str
    created_at: Optional[datetime]


@router.get("/users")
def admin_users_list(
    q: Optional[str] = Query(None),
    role: Optional[UserRole] = Query(None),
    status: Optional[UserStatus] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_admin=Depends(get_current_user),
    db=Depends(get_db),
):
    """Paginated user listing with optional search and filters.

    Searches email and company_name when available.
    """
    try:
        stmt = select(User)
        filters = []
        if role is not None:
            filters.append(User.role == role)
        if status is not None:
            filters.append(User.status == status)
        if q:
            # Search email and company_name
            ilike_q = f"%{q}%"
            try:
                filters.append(
                    or_(User.email.ilike(ilike_q), User.company_name.ilike(ilike_q))
                )
            except Exception as e:
                logging.debug("company_name not present or ilike failed", exc_info=False)
                # Fallback: try email only
                try:
                    filters.append(User.email.ilike(ilike_q))
                except Exception as ee:
                    logging.error(ee, exc_info=True)
        if filters:
            stmt = stmt.where(and_(*filters))

        # Ordering: created_at desc if present else id desc
        try:
            stmt = stmt.order_by(User.created_at.desc())
        except Exception:
            stmt = stmt.order_by(User.id.desc())

        total_stmt = select(func.count()).select_from(stmt.subquery())
        total = db.execute(total_stmt).scalar_one()

        stmt = stmt.limit(limit).offset(offset)
        rows = db.execute(stmt).scalars().all()

        items = [
            {
                "id": r.id,
                "email": getattr(r, "email", None),
                "role": r.role.value if hasattr(r.role, "value") else str(r.role),
                "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                "created_at": r.created_at,
            }
            for r in rows
        ]

        # Audit
        details = {
            "q": q,
            "role": role.value if role else None,
            "status": status.value if status else None,
            "limit": limit,
            "offset": offset,
        }
        write_admin_audit(
            db,
            admin_user_id=current_admin.id,
            action_type="USERS_LIST",
            target_type="user",
            target_id=None,
            details_json=details,
        )
        db.commit()

        return {"total": int(total or 0), "items": items}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="failed to list users")


class ModerationBody(BaseModel):
    reason: Optional[str] = None


def _prevent_self_action(current_admin, target_user_id: str) -> None:
    if hasattr(current_admin, "id") and current_admin.id == target_user_id:
        raise HTTPException(status_code=400, detail="cannot moderate yourself")


@router.post("/users/{user_id}/suspend")
def admin_user_suspend(
    user_id: str = Path(...),
    body: Optional[ModerationBody] = Body(default=None),
    current_admin=Depends(get_current_user),
    db=Depends(get_db),
):
    try:
        _prevent_self_action(current_admin, user_id)
        stmt = select(User).where(User.id == user_id)
        user = db.execute(stmt).scalars().first()
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")

        prev = user.status.value if hasattr(user.status, "value") else str(user.status)
        new_status = UserStatus.suspended
        user.status = new_status
        # Flush so we can write audit with previous/new status in same transaction
        db.flush()

        details = {"previous_status": prev, "new_status": new_status.value, "reason": (body.reason if body else None)}
        write_admin_audit(
            db,
            admin_user_id=current_admin.id,
            action_type="USER_SUSPEND",
            target_type="user",
            target_id=user_id,
            details_json=details,
        )
        db.commit()
        return {"id": user_id, "status": new_status.value}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="failed to suspend user")


@router.post("/users/{user_id}/ban")
def admin_user_ban(
    user_id: str = Path(...),
    body: Optional[ModerationBody] = Body(default=None),
    current_admin=Depends(get_current_user),
    db=Depends(get_db),
):
    try:
        _prevent_self_action(current_admin, user_id)
        stmt = select(User).where(User.id == user_id)
        user = db.execute(stmt).scalars().first()
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")

        prev = user.status.value if hasattr(user.status, "value") else str(user.status)
        new_status = UserStatus.banned
        user.status = new_status
        db.flush()

        details = {"previous_status": prev, "new_status": new_status.value, "reason": (body.reason if body else None)}
        write_admin_audit(
            db,
            admin_user_id=current_admin.id,
            action_type="USER_BAN",
            target_type="user",
            target_id=user_id,
            details_json=details,
        )
        db.commit()
        return {"id": user_id, "status": new_status.value}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="failed to ban user")


# New: admin jobs listing endpoint
class JobListItem(BaseModel):
    id: str
    title: Optional[str]
    created_at: Optional[datetime]
    is_removed: bool


@router.get("/jobs")
def admin_jobs_list(
    q: Optional[str] = Query(None),
    removed: Optional[bool] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_admin=Depends(get_current_user),
    db=Depends(get_db),
):
    """Admin-only listing of jobs with optional search and removed filter.

    Writes audit log on success.
    """
    try:
        stmt = select(Job)
        filters = []

        if removed is not None:
            # filter by is_removed boolean
            if removed:
                filters.append(Job.is_removed.is_(True))
            else:
                filters.append(Job.is_removed.is_(False))

        if q:
            ilike_q = f"%{q}%"
            # Try to search title and company.company_name when available
            try:
                # Build filter first to avoid partial query state on failure
                search_filter = or_(Job.title.ilike(ilike_q), User.company_name.ilike(ilike_q))
                stmt = stmt.join(Job.company)
                filters.append(search_filter)
            except Exception as e:
                logging.error(e, exc_info=True)
                # Fallback: only title
                try:
                    filters.append(Job.title.ilike(ilike_q))
                except Exception as e:
                    logging.error(e, exc_info=True)

        if filters:
            stmt = stmt.where(and_(*filters))

        # Ordering: created_at desc if present else id desc
        try:
            stmt = stmt.order_by(Job.created_at.desc())
        except Exception:
            stmt = stmt.order_by(Job.id.desc())

        total_stmt = select(func.count()).select_from(stmt.subquery())
        total = db.execute(total_stmt).scalar_one()

        stmt = stmt.limit(limit).offset(offset)
        rows = db.execute(stmt).scalars().all()

        items = [
            {
                "id": r.id,
                "title": getattr(r, "title", None),
                "created_at": getattr(r, "created_at", None),
                "is_removed": bool(getattr(r, "is_removed", False)),
            }
            for r in rows
        ]

        # Audit
        details = {"q": q, "removed": removed, "limit": limit, "offset": offset}
        write_admin_audit(
            db,
            admin_user_id=current_admin.id,
            action_type="JOBS_LIST",
            target_type="job",
            target_id=None,
            details_json=details,
        )
        db.commit()

        return {"total": int(total or 0), "items": items}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(e, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="failed to list jobs")
