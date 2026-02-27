from __future__ import annotations

import logging
import threading
from typing import Callable

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception as e:  # defensive: surface clearer import error
    logging.getLogger(__name__).error(e, exc_info=True)
    raise

logger = logging.getLogger(__name__)

# Singleton scheduler instance for the process
_scheduler = BackgroundScheduler()
_lock = threading.Lock()
_started = False
_start_count = 0  # visible for tests to assert single-start semantics


def start_scheduler() -> None:
    """Start the in-process scheduler. Idempotent and thread-safe."""
    global _started, _start_count
    with _lock:
        if _started:
            return
        try:
            _scheduler.start()
            _started = True
            _start_count += 1
            logger.info("Scheduler started")
        except Exception as e:
            logger.error(e, exc_info=True)
            raise


def shutdown_scheduler() -> None:
    """Shutdown the scheduler if it was started. Idempotent and safe if never started."""
    global _started
    with _lock:
        if not _started:
            # Nothing to stop; behave idempotently
            return
        try:
            # APScheduler.shutdown may raise if already stopped; we guard with _started
            _scheduler.shutdown(wait=True)
            _started = False
            logger.info("Scheduler stopped")
        except Exception as e:
            logger.error(e, exc_info=True)
            # Do not raise to keep shutdown tolerant


def is_running() -> bool:
    """Return True if scheduler was started and not yet shutdown."""
    return _started


def get_start_count() -> int:
    """Return how many times the scheduler start sequence executed. For tests only."""
    return _start_count
