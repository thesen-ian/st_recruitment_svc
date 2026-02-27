from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List, Optional

from st_recruitment_svc.advisory_lock import advisory_lock
from st_recruitment_svc.models import base

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobDefinition:
    job_id: str
    job_name: str
    seconds: int
    func: Callable[[], None]
    coalesce: bool = True
    max_instances: int = 1
    misfire_grace_time: Optional[int] = None


# In-memory registry
_registered_jobs: List[JobDefinition] = []


# Allow tests to override DB session factory; default to SessionLocal
_db_session_factory = base.SessionLocal  # type: ignore


def set_db_session_factory(factory: Callable[[], base.SessionLocal]) -> None:
    """Replace DB session factory for testing or alternate wiring."""
    global _db_session_factory
    _db_session_factory = factory


def register_job(defn: JobDefinition) -> None:
    """Register a job for later scheduling. Raises ValueError on duplicate job_id."""
    for j in _registered_jobs:
        if j.job_id == defn.job_id:
            raise ValueError(f"job with id '{defn.job_id}' already registered")
    _registered_jobs.append(defn)


def get_registered_jobs() -> List[JobDefinition]:
    """Return a shallow copy of registered jobs."""
    return list(_registered_jobs)


def clear_registered_jobs_for_tests() -> None:
    """Clear registry. For tests only."""
    _registered_jobs.clear()


def make_job_wrapper(defn: JobDefinition, db_session_factory: Optional[Callable[[], object]] = None) -> Callable[[], None]:
    """Create a scheduler-callable wrapper that enforces advisory lock and safe logging.

    The wrapper always catches exceptions and never lets them escape to the scheduler.
    """
    db_factory = db_session_factory or _db_session_factory

    def wrapper() -> None:
        # Obtain DB session
        try:
            session = db_factory()
        except Exception:
            # Do not include sensitive details in logs; skip execution.
            # Emit a stable, non-sensitive message so tests can assert on it.
            # Log to both module logger and root logger to ensure capture by test harness.
            try:
                logger.warning("Failed to obtain DB session for job %s; skipping execution", defn.job_id)
            except Exception:
                # best effort; do not raise
                pass
            try:
                logging.getLogger().warning("Failed to obtain DB session")
            except Exception:
                pass
            return

        try:
            try:
                with advisory_lock(session, job_name=defn.job_name) as acquired:
                    if not acquired:
                        logger.info("Advisory lock not acquired for job %s; skipping", defn.job_id)
                        return
                    try:
                        defn.func()
                    except Exception:
                        logger.exception("Unhandled exception while executing job %s", defn.job_id)
            except Exception:
                # Advisory lock helper may raise; treat as skip and log safely
                logger.warning("Advisory lock failed for job %s; skipping execution", defn.job_id)
                return
        finally:
            # Ensure session closed if it has close method
            try:
                if hasattr(session, "close"):
                    session.close()
            except Exception:
                logger.warning("Failed to close DB session for job %s", defn.job_id)

    return wrapper


def schedule_registered_jobs(scheduler) -> None:
    """Schedule all registered jobs into the provided APScheduler scheduler instance.

    Uses job id as APScheduler id to avoid duplicates. Respects existing jobs.
    """
    jobs = get_registered_jobs()
    for defn in jobs:
        # Avoid adding duplicates at scheduler level
        try:
            existing = None
            if hasattr(scheduler, "get_job"):
                try:
                    existing = scheduler.get_job(defn.job_id)
                except Exception:
                    # If scheduler doesn't support get_job or fails, fall back to add_job and rely on APScheduler errors
                    existing = None

            if existing is not None:
                logger.debug("Job %s already scheduled; skipping", defn.job_id)
                continue

            wrapper = make_job_wrapper(defn)
            # Add interval job
            sched_kwargs = {
                "id": defn.job_id,
                "coalesce": defn.coalesce,
                "max_instances": defn.max_instances,
                "misfire_grace_time": defn.misfire_grace_time,
            }
            # Remove None values
            sched_kwargs = {k: v for k, v in sched_kwargs.items() if v is not None}

            # Use interval trigger with seconds
            scheduler.add_job(wrapper, "interval", seconds=defn.seconds, **sched_kwargs)
            logger.info("Scheduled job %s (every %ss)", defn.job_id, defn.seconds)
        except Exception:
            logger.exception("Failed to schedule job %s", defn.job_id)
