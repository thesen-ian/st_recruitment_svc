from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from st_recruitment_svc.auth import get_current_user
from st_recruitment_svc.models.base import get_db, Notification

logger = logging.getLogger(__name__)
router = APIRouter()


class NotificationOut(BaseModel):
    id: str
    user_id: str
    title: str
    body: str
    type: str
    related_entity_type: Optional[str] = None
    related_entity_id: Optional[str] = None
    is_read: bool
    read_at: Optional[str] = None
    created_at: str
    updated_at: str


class ReadAllOut(BaseModel):
    updated: int


def _serialize(n: Notification) -> dict:
    return {
        "id": n.id,
        "user_id": n.user_id,
        "title": n.title,
        "body": n.body,
        "type": n.type,
        "related_entity_type": n.related_entity_type,
        "related_entity_id": n.related_entity_id,
        "is_read": bool(n.is_read),
        "read_at": n.read_at.isoformat() if getattr(n, "read_at", None) is not None else None,
        "created_at": n.created_at.isoformat() if getattr(n, "created_at", None) is not None else None,
        "updated_at": n.updated_at.isoformat() if getattr(n, "updated_at", None) is not None else None,
    }


@router.get("/notifications")
def list_notifications(
    filter: str = Query("all"),
    limit: int = Query(50),
    offset: int = Query(0),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """List notifications for current user with optional filter/unread and pagination."""
    if filter not in ("all", "unread"):
        raise HTTPException(status_code=422, detail="invalid filter")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")

    try:
        # Build statement applying filtering and pagination at the SQL level to avoid
        # loading all rows into memory for users with many notifications.
        stmt = select(Notification).where(Notification.user_id == current_user.id)
        if filter == "unread":
            # Use read_at IS NULL which is portable across DB backends
            stmt = stmt.where(Notification.read_at.is_(None))
        stmt = stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
        rows = db.execute(stmt).scalars().all()
    except Exception:
        logger.exception("DB error listing notifications for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="Internal error")

    items: List[dict] = [_serialize(n) for n in rows]
    return {"items": items, "limit": limit, "offset": offset}


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Mark a single notification as read for the authenticated user.

    Implemented using an UPDATE followed by SELECT to avoid session state issues
    across DB backends and ensure idempotency and ownership enforcement.
    """
    try:
        # Attempt to atomically mark unread notification as read. Use read_at IS NULL to
        # reliably detect unread rows across SQLite/Postgres.
        now = datetime.now(tz=timezone.utc)
        upd = (
            update(Notification)
            .where(Notification.id == notification_id, Notification.user_id == current_user.id, Notification.read_at.is_(None))
            .values(is_read=True, read_at=now)
        )
        res = db.execute(upd)
        db.commit()

        if res.rowcount == 1:
            # Successfully updated an unread notification; fetch fresh row to return
            stmt = select(Notification).where(Notification.id == notification_id, Notification.user_id == current_user.id)
            n = db.execute(stmt).scalars().first()
            if n is None:
                # Extremely unlikely: updated then missing
                raise HTTPException(status_code=500, detail="Internal error")
            return _serialize(n)

        # If no rows updated, either the notification does not exist for this user
        # or it's already read. Load the row to disambiguate.
        stmt = select(Notification).where(Notification.id == notification_id, Notification.user_id == current_user.id)
        n = db.execute(stmt).scalars().first()
    except HTTPException:
        raise
    except Exception:
        logger.exception("DB error loading/updating notification %s for user %s", notification_id, current_user.id)
        try:
            db.rollback()
        except Exception:
            logger.exception("rollback failed after notification update error")
        raise HTTPException(status_code=500, detail="Internal error")

    if n is None:
        # Do not reveal existence for other users
        raise HTTPException(status_code=404, detail="notification not found")

    # Already read case: idempotent
    return _serialize(n)


@router.post("/notifications/read-all", response_model=ReadAllOut)
def mark_all_read(current_user=Depends(get_current_user), db: Session = Depends(get_db)) -> Any:
    """Mark all unread notifications for the current user as read in a single UPDATE."""
    try:
        now = datetime.now(tz=timezone.utc)
        # Use read_at IS NULL to identify unread rows which is portable across DB backends
        upd = (
            update(Notification)
            .where(Notification.user_id == current_user.id, Notification.read_at.is_(None))
            .values(is_read=True, read_at=now)
        )
        res = db.execute(upd)
        db.commit()
        updated = int(res.rowcount or 0)
    except Exception:
        logger.exception("Failed to mark all notifications read for user %s", current_user.id)
        try:
            db.rollback()
        except Exception:
            logger.exception("rollback failed after bulk read-all failure")
        raise HTTPException(status_code=500, detail="failed to mark notifications read")

    return {"updated": updated}
