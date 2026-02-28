from __future__ import annotations

import logging
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from st_recruitment_svc.models.admin import AdminAuditLog

logger = logging.getLogger(__name__)


def write_admin_audit(
    session: Session,
    admin_user_id: str,
    action_type: str,
    target_type: str,
    target_id: Optional[str] = None,
    details_json: Optional[Dict[str, Any]] = None,
) -> AdminAuditLog:
    """Create an AdminAuditLog record and flush it to the provided session.

    Does not commit so callers can control transaction boundaries. Flushes so
    callers can query the newly-created row within the same session.
    """
    try:
        audit = AdminAuditLog(
            admin_user_id=admin_user_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            details_json=details_json,
        )
        session.add(audit)
        # Flush to persist to DB within the current transaction without committing
        session.flush()
        return audit
    except Exception as e:
        logger.error("Failed to write admin audit log for admin_user_id=%s: %s", admin_user_id, e, exc_info=True)
        raise
