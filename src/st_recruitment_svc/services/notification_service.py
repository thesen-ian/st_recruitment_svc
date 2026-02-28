from __future__ import annotations

import logging
import uuid
from typing import Optional
from sqlalchemy import select
from sqlalchemy.engine import Connection
from st_recruitment_svc.models.base import Notification, NotificationPreferences

logger = logging.getLogger(__name__)

# Map notification types to preference column names
PREF_FIELD_BY_TYPE = {
    "application_submitted": "notify_application_submitted",
    "application_status_changed": "notify_application_status_changed",
    "application_withdrawn": "notify_application_withdrawn",
    # Use canonical interview_updated type per spec and map it to the notify_interview_updates pref
    "interview_updated": "notify_interview_updates",
    "admin_report_updates": "notify_admin_report_updates",
}


def _load_prefs_row(conn: Connection, user_id: str) -> Optional[dict]:
    """Load preference row as dict using a core connection. Returns None when missing.

    Use core table column selection and .mappings() so this works when given a raw
    Connection (e.g. inside mapper event handlers).
    """
    try:
        t = NotificationPreferences.__table__
        stmt = select(
            t.c.in_app_enabled,
            t.c.notify_application_submitted,
            t.c.notify_application_status_changed,
            t.c.notify_application_withdrawn,
            t.c.notify_interview_updates,
            t.c.notify_admin_report_updates,
        ).where(t.c.user_id == user_id)
        res = conn.execute(stmt)
        row = res.mappings().first()
        if row is None:
            return None
        return {
            "in_app_enabled": bool(row.get("in_app_enabled", True)),
            "notify_application_submitted": bool(row.get("notify_application_submitted", True)),
            "notify_application_status_changed": bool(row.get("notify_application_status_changed", True)),
            "notify_application_withdrawn": bool(row.get("notify_application_withdrawn", True)),
            "notify_interview_updates": bool(row.get("notify_interview_updates", True)),
            "notify_admin_report_updates": bool(row.get("notify_admin_report_updates", True)),
        }
    except Exception as e:
        logger.error("Failed to load notification preferences for user %s: %s", user_id, e, exc_info=True)
        return None


def should_send_in_app(conn: Connection, user_id: str, notif_type: str) -> bool:
    """Return True when an in-app notification of notif_type should be created for user_id.

    If preferences row is missing, treat as defaults (all enabled by spec).
    """
    try:
        pref = _load_prefs_row(conn, user_id)
        if pref is None:
            return True
        if not pref.get("in_app_enabled", True):
            return False
        pref_field = PREF_FIELD_BY_TYPE.get(notif_type)
        if pref_field is None:
            # Unknown type: be conservative and send
            return True
        return bool(pref.get(pref_field, True))
    except Exception as e:
        logger.error("Error evaluating preference for user %s type %s: %s", user_id, notif_type, e, exc_info=True)
        # On error, default to True so we don't accidentally suppress important notifications
        return True


def create_in_app_notification(
    conn: Connection,
    user_id: str,
    notif_type: str,
    title: str,
    body: str,
    related_entity_type: Optional[str] = None,
    related_entity_id: Optional[str] = None,
) -> Optional[str]:
    """Create a Notification row using the provided Connection if preferences allow.

    Returns inserted notification id when created, otherwise None.

    This function performs a core INSERT using the provided Connection so it can be
    safely called from within SQLAlchemy mapper event handlers that provide a
    Connection. It intentionally does not create a separate Session or commit
    outside the caller's transaction to avoid breaking atomicity.
    """
    try:
        if not should_send_in_app(conn, user_id, notif_type):
            logger.debug("Notification suppressed by preferences for user=%s type=%s", user_id, notif_type)
            return None

        # Ensure an id is provided for core insert to avoid relying on ORM-level python default
        nid = str(uuid.uuid4())
        ins = Notification.__table__.insert().values(
            id=nid,
            user_id=user_id,
            title=title[:255] if title is not None else "",
            body=body or "",
            type=notif_type,
            related_entity_type=(related_entity_type or None),
            related_entity_id=(related_entity_id or None),
        )
        try:
            conn.execute(ins)
            # rely on surrounding transaction to persist; return id for observability
            logger.info("Created in-app notification for user %s type %s id=%s via core insert", user_id, notif_type, nid)
            return nid
        except Exception as e:
            # Do not commit using a separate session here; log and return None so the
            # main transactional flow is not made inconsistent by an out-of-band commit.
            logger.error("Core insert failed for notification user=%s type=%s: %s", user_id, notif_type, e, exc_info=True)
            return None
    except Exception as e:
        logger.error("Failed to create in-app notification for user %s type %s: %s", user_id, notif_type, e, exc_info=True)
        return None
