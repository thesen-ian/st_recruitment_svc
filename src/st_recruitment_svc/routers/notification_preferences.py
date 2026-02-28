from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from st_recruitment_svc.auth import get_current_user
from st_recruitment_svc.models.base import get_db, NotificationPreferences

logger = logging.getLogger(__name__)
router = APIRouter()


class NotificationPreferencesOut(BaseModel):
    id: str
    user_id: str
    in_app_enabled: bool
    notify_application_submitted: bool
    notify_application_status_changed: bool
    notify_application_withdrawn: bool
    notify_interview_updates: bool
    notify_admin_report_updates: bool
    created_at: str
    updated_at: str


class NotificationPreferencesUpdateIn(BaseModel):
    in_app_enabled: Optional[bool] = None
    notify_application_submitted: Optional[bool] = None
    notify_application_status_changed: Optional[bool] = None
    notify_application_withdrawn: Optional[bool] = None
    notify_interview_updates: Optional[bool] = None
    notify_admin_report_updates: Optional[bool] = None


def _serialize(pref: NotificationPreferences) -> dict:
    return {
        "id": pref.id,
        "user_id": pref.user_id,
        "in_app_enabled": bool(pref.in_app_enabled),
        "notify_application_submitted": bool(pref.notify_application_submitted),
        "notify_application_status_changed": bool(pref.notify_application_status_changed),
        "notify_application_withdrawn": bool(pref.notify_application_withdrawn),
        "notify_interview_updates": bool(pref.notify_interview_updates),
        "notify_admin_report_updates": bool(pref.notify_admin_report_updates),
        "created_at": pref.created_at.isoformat() if getattr(pref, "created_at", None) is not None else None,
        "updated_at": pref.updated_at.isoformat() if getattr(pref, "updated_at", None) is not None else None,
    }


@router.get("/notification-preferences/me", response_model=NotificationPreferencesOut)
def get_my_notification_preferences(
    current_user=Depends(get_current_user), db: Session = Depends(get_db)
) -> Any:
    try:
        stmt = select(NotificationPreferences).where(NotificationPreferences.user_id == current_user.id)
        pref = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading notification preferences for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="Internal error")

    if pref is None:
        # Create defaults using model defaults
        try:
            pref = NotificationPreferences(user_id=current_user.id)
            db.add(pref)
            db.commit()
            db.refresh(pref)
        except IntegrityError:
            # Race: another request created it. Rollback and retry fetch once.
            try:
                db.rollback()
            except Exception:
                logger.exception("rollback failed after integrity error creating prefs for %s", current_user.id)
            try:
                stmt = select(NotificationPreferences).where(NotificationPreferences.user_id == current_user.id)
                pref = db.execute(stmt).scalars().first()
            except Exception:
                logger.exception("DB error reloading notification preferences after race for user %s", current_user.id)
                raise HTTPException(status_code=500, detail="Internal error")
            if pref is None:
                raise HTTPException(status_code=500, detail="failed to create notification preferences")
        except Exception:
            logger.exception("Failed to create notification preferences for user %s", current_user.id)
            try:
                db.rollback()
            except Exception:
                logger.exception("rollback failed after create failure")
            raise HTTPException(status_code=500, detail="failed to create notification preferences")

    return _serialize(pref)


@router.put("/notification-preferences/me", response_model=NotificationPreferencesOut)
def put_my_notification_preferences(
    payload: NotificationPreferencesUpdateIn,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    # Partial update semantics: only provided fields are changed
    try:
        stmt = select(NotificationPreferences).where(NotificationPreferences.user_id == current_user.id)
        pref = db.execute(stmt).scalars().first()
    except Exception:
        logger.exception("DB error loading notification preferences for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="Internal error")

    if pref is None:
        # Create new row applying provided fields and letting DB defaults fill others
        try:
            to_create = NotificationPreferences(user_id=current_user.id)
            # Apply provided optional fields
            for name, val in (payload.model_dump().items() if hasattr(payload, "model_dump") else payload.dict().items()):
                if val is not None:
                    setattr(to_create, name, val)
            db.add(to_create)
            db.commit()
            db.refresh(to_create)
            pref = to_create
            # Return immediately after successful create to avoid redundant update logic
            return _serialize(pref)
        except IntegrityError:
            try:
                db.rollback()
            except Exception:
                logger.exception("rollback failed after integrity error creating prefs for %s", current_user.id)
            # Another request created it, fetch and apply update
            try:
                stmt = select(NotificationPreferences).where(NotificationPreferences.user_id == current_user.id)
                pref = db.execute(stmt).scalars().first()
            except Exception:
                logger.exception("DB error reloading notification preferences after race for user %s", current_user.id)
                raise HTTPException(status_code=500, detail="Internal error")
            if pref is None:
                raise HTTPException(status_code=500, detail="failed to create notification preferences")
        except Exception:
            logger.exception("Failed to create notification preferences for user %s", current_user.id)
            try:
                db.rollback()
            except Exception:
                logger.exception("rollback failed after create failure")
            raise HTTPException(status_code=500, detail="failed to create notification preferences")

    # Apply updates for existing pref (or pref obtained after race)
    updated = False
    try:
        data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
        for field, val in data.items():
            if val is not None and getattr(pref, field, None) != val:
                setattr(pref, field, val)
                updated = True
        if updated:
            try:
                pref.updated_at = datetime.now(tz=timezone.utc)
            except Exception:
                pass
            db.add(pref)
            db.commit()
            db.refresh(pref)
    except Exception:
        logger.exception("Failed to update notification preferences for user %s", current_user.id)
        try:
            db.rollback()
        except Exception:
            logger.exception("rollback failed after update failure")
        raise HTTPException(status_code=500, detail="failed to update notification preferences")

    return _serialize(pref)
