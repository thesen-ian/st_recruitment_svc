from __future__ import annotations

import logging
import uuid
from sqlalchemy import select, event, and_, func
from sqlalchemy.engine import Connection
from sqlalchemy import inspect as sa_inspect

from st_recruitment_svc.models.base import (
    Application,
    JobPosting,
    Notification,
    NotificationPreferences,
    ApplicationStatusHistory,
    Interview,
    ApplicationStatus,
    ApplicationStatus as AppStatusEnum,
)
from st_recruitment_svc.services.notification_service import create_in_app_notification

logger = logging.getLogger(__name__)

# NOTE: admin_report_updates hooks are omitted because recruitment-service currently
# does not expose a clear admin report domain hook. If/when a report workflow exists,
# wire create_in_app_notification(...) at that point. This is intentional per spec.


def _fetch_job_by_id(conn: Connection, job_id: str) -> dict | None:
    """Load minimal job fields using core column selection to work with Connection.

    Avoid ORM-mapped object retrieval via raw Connection; use core table columns
    and .mappings() so this works inside mapper event handlers.
    """
    try:
        jt = JobPosting.__table__
        stmt = select(jt.c.company_id, jt.c.title, jt.c.id).where(jt.c.id == job_id)
        r = conn.execute(stmt)
        row = r.mappings().first()
        if row is None:
            return None
        return {"company_id": row["company_id"], "title": row.get("title"), "id": row.get("id")}
    except Exception as e:
        logger.error("Error loading job %s in notification listener: %s", job_id, e, exc_info=True)
        return None


def _fetch_application_by_id(conn: Connection, application_id: str) -> dict | None:
    """Load minimal application fields via core column selection."""
    try:
        at = Application.__table__
        stmt = select(at.c.job_id, at.c.job_seeker_user_id, at.c.id).where(at.c.id == application_id)
        r = conn.execute(stmt)
        row = r.mappings().first()
        if row is None:
            return None
        return {"job_id": row.get("job_id"), "job_seeker_user_id": row.get("job_seeker_user_id"), "id": row.get("id")}
    except Exception as e:
        logger.error("Error loading application %s in notification listener: %s", application_id, e, exc_info=True)
        return None


def _already_notified(conn: Connection, user_id: str, notif_type: str, related_entity_type: str | None, related_entity_id: str | None) -> bool:
    """Return True when an equivalent notification already exists to avoid duplicates.

    We consider duplication by exact user/type/related_entity triplet. This is a simple
    dedup heuristic sufficient to avoid duplicate notifications when listeners fire
    multiple times in a single transaction/flush.
    """
    try:
        nt = Notification.__table__
        stmt = select(func.count()).where(
            nt.c.user_id == user_id,
            nt.c.type == notif_type,
            nt.c.related_entity_type == (related_entity_type or None),
            nt.c.related_entity_id == (related_entity_id or None),
        )
        r = conn.execute(stmt)
        cnt = r.scalar()
        return bool(cnt and cnt > 0)
    except Exception as e:
        logger.error("Failed duplicate check for notification user=%s type=%s: %s", user_id, notif_type, e, exc_info=True)
        # Be defensive: if we can't tell, assume not notified to avoid skipping legitimate notifications
        return False


# Application submitted: after_insert on Application
@event.listens_for(Application, "after_insert")
def _on_application_insert(mapper, connection: Connection, target: Application):
    try:
        app_id = getattr(target, "id", None)
        job_id = getattr(target, "job_id", None)
        seeker_id = getattr(target, "job_seeker_user_id", None)
        if not job_id or not seeker_id:
            return

        job = _fetch_job_by_id(connection, job_id)
        if job is None:
            return
        company_id = job.get("company_id")
        job_title = job.get("title") or "job"

        # Company-side notification
        if company_id:
            if not _already_notified(connection, company_id, "application_submitted", "application", app_id):
                title = f"New application for {job_title}"
                body = f"A candidate has applied to {job_title}."
                try:
                    create_in_app_notification(connection, company_id, "application_submitted", title, body, "application", app_id)
                except Exception:
                    logger.exception("Failed to create company-side application_submitted notification")

        # Candidate confirmation
        if not _already_notified(connection, seeker_id, "application_submitted", "application", app_id):
            title2 = f"Application submitted to {job_title}"
            body2 = f"Your application to {job_title} was submitted."
            try:
                create_in_app_notification(connection, seeker_id, "application_submitted", title2, body2, "application", app_id)
            except Exception:
                logger.exception("Failed to create seeker-side application_submitted notification")

    except Exception as e:
        logger.error("Error handling application insert notification: %s", e, exc_info=True)


# Application status/history changes
@event.listens_for(ApplicationStatusHistory, "after_insert")
def _on_status_history_insert(mapper, connection: Connection, target: ApplicationStatusHistory):
    try:
        app_id = getattr(target, "application_id", None)
        from_status = getattr(target, "from_status", None)
        to_status = getattr(target, "to_status", None)
        notes = getattr(target, "notes", "") or ""
        changed_by = getattr(target, "changed_by_user_id", None)
        if not app_id or not to_status:
            return

        app = _fetch_application_by_id(connection, app_id)
        if app is None:
            return
        seeker_id = app.get("job_seeker_user_id")
        job = _fetch_job_by_id(connection, app.get("job_id"))
        company_id = job.get("company_id") if job else None

        # Detect withdraw more robustly: prefer explicit notes marker or seeker-initiated rejection
        is_withdrawn = False
        try:
            if to_status == ApplicationStatus.Rejected.value and changed_by and seeker_id and changed_by == seeker_id:
                # Prefer explicit 'withdrawn' marker in notes
                if "withdrawn" in (notes or "").lower():
                    is_withdrawn = True
                else:
                    # If no explicit marker, treat a seeker-initiated rejection from early workflow states as a withdraw
                    early_states = {ApplicationStatus.Applied.value, ApplicationStatus.ResumeReview.value, ApplicationStatus.PhoneScreen.value}
                    if from_status in early_states:
                        is_withdrawn = True
        except Exception:
            if "withdrawn" in (notes or "").lower():
                is_withdrawn = True

        if is_withdrawn:
            notif_type = "application_withdrawn"
            title = f"Application withdrawn"
            body = f"An application was withdrawn (status: {from_status} -> {to_status})."
        else:
            notif_type = "application_status_changed"
            title = f"Application status updated"
            body = f"Application status changed from {from_status} to {to_status}."

        # Notify seeker (subject to their prefs)
        if seeker_id:
            if not _already_notified(connection, seeker_id, notif_type, "application", app_id):
                try:
                    create_in_app_notification(connection, seeker_id, notif_type, title, body, "application", app_id)
                except Exception:
                    logger.exception("Failed to create seeker notification for status change")

        # Notify company
        if company_id:
            job_title = job.get("title") if job else ""
            if not _already_notified(connection, company_id, notif_type, "application", app_id):
                try:
                    create_in_app_notification(connection, company_id, notif_type, f"{title} - {job_title}", body, "application", app_id)
                except Exception:
                    logger.exception("Failed to create company notification for status change")

    except Exception as e:
        logger.error("Error handling status history insert notification: %s", e, exc_info=True)


# Interview created/proposed
@event.listens_for(Interview, "after_insert")
def _on_interview_insert(mapper, connection: Connection, target: Interview):
    try:
        interview_id = getattr(target, "id", None)
        application_id = getattr(target, "application_id", None)
        if not interview_id or not application_id:
            return

        app = _fetch_application_by_id(connection, application_id)
        if app is None:
            return
        seeker_id = app.get("job_seeker_user_id")
        job = _fetch_job_by_id(connection, app.get("job_id"))
        company_id = job.get("company_id") if job else None

        title = "Interview proposed"
        body = "An interview has been proposed."

        if seeker_id:
            if not _already_notified(connection, seeker_id, "interview_updates", "interview", interview_id):
                try:
                    create_in_app_notification(connection, seeker_id, "interview_updates", title, body, "interview", interview_id)
                except Exception:
                    logger.exception("Failed to create seeker interview notification")
        if company_id:
            if not _already_notified(connection, company_id, "interview_updates", "interview", interview_id):
                try:
                    create_in_app_notification(connection, company_id, "interview_updates", title, body, "interview", interview_id)
                except Exception:
                    logger.exception("Failed to create company interview notification")

    except Exception as e:
        logger.error("Error handling interview insert notification: %s", e, exc_info=True)


# Interview updates: only notify on status transitions to avoid noise
@event.listens_for(Interview, "after_update")
def _on_interview_update(mapper, connection: Connection, target: Interview):
    try:
        # Only act when the status attribute actually changed
        try:
            insp = sa_inspect(target)
            if not insp.attrs.status.history.has_changes():
                return
        except Exception:
            # If inspection fails, be conservative and proceed
            pass

        interview_id = getattr(target, "id", None)
        application_id = getattr(target, "application_id", None)
        if not interview_id or not application_id:
            return

        app = _fetch_application_by_id(connection, application_id)
        if app is None:
            return
        seeker_id = app.get("job_seeker_user_id")
        job = _fetch_job_by_id(connection, app.get("job_id"))
        company_id = job.get("company_id") if job else None

        title = "Interview updated"
        body = "Interview details or status were updated."

        if seeker_id:
            if not _already_notified(connection, seeker_id, "interview_updates", "interview", interview_id):
                try:
                    create_in_app_notification(connection, seeker_id, "interview_updates", title, body, "interview", interview_id)
                except Exception:
                    logger.exception("Failed to create seeker interview update notification")
        if company_id:
            if not _already_notified(connection, company_id, "interview_updates", "interview", interview_id):
                try:
                    create_in_app_notification(connection, company_id, "interview_updates", title, body, "interview", interview_id)
                except Exception:
                    logger.exception("Failed to create company interview update notification")

    except Exception as e:
        logger.error("Error handling interview update notification: %s", e, exc_info=True)
