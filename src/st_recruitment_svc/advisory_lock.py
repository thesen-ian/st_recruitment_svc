from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def advisory_lock_key(job_name: str) -> int:
    """Deterministically map job_name to a signed 64-bit integer key.

    Uses SHA-256 and maps the first 8 bytes into signed int64 range.
    """
    h = hashlib.sha256(job_name.encode("utf-8")).digest()
    first8 = h[:8]
    unsigned = int.from_bytes(first8, byteorder="big", signed=False)
    # Map to signed int64 (two's complement)
    if unsigned >= 2 ** 63:
        signed = unsigned - 2 ** 64
    else:
        signed = unsigned
    return signed


def _execute_and_extract_bool(db: Session, stmt, params: dict) -> bool:
    try:
        res = db.execute(stmt, params)
        # Support both ResultProxy-like objects with scalar() and direct booleans
        if hasattr(res, "scalar"):
            val = res.scalar()
        elif hasattr(res, "scalar_one"):
            try:
                val = res.scalar_one()
            except Exception:
                # be conservative
                val = None
        else:
            val = res
        return bool(val)
    except Exception as e:
        raise


def try_acquire_advisory_lock(db: Session, *, lock_key: int) -> bool:
    """Attempt to acquire Postgres advisory lock identified by lock_key.

    On DB error returns False and logs a short message.
    """
    try:
        stmt = text("SELECT pg_try_advisory_lock(:key)")
        acquired = _execute_and_extract_bool(db, stmt, {"key": lock_key})
        return acquired
    except Exception as e:
        # Do not include sensitive connection info; include only key in logs.
        logger.warning("Failed to acquire advisory lock key=%s: %s", lock_key, str(e), exc_info=False)
        return False


def release_advisory_lock(db: Session, *, lock_key: int) -> bool:
    """Attempt to release Postgres advisory lock identified by lock_key.

    Returns True if released, False otherwise. DB errors are caught and logged.
    """
    try:
        stmt = text("SELECT pg_advisory_unlock(:key)")
        released = _execute_and_extract_bool(db, stmt, {"key": lock_key})
        return released
    except Exception as e:
        logger.warning("Failed to release advisory lock key=%s: %s", lock_key, str(e), exc_info=False)
        return False


@contextmanager
def advisory_lock(db: Session, *, job_name: str) -> Generator[bool, None, None]:
    """Context manager that attempts to acquire an advisory lock for job_name.

    Yields True if lock acquired, False otherwise. Release is best-effort on exit.
    Exceptions during acquire/release are caught and logged without raising.
    """
    key = advisory_lock_key(job_name)
    try:
        acquired = try_acquire_advisory_lock(db, lock_key=key)
    except Exception:
        # try_acquire_advisory_lock already logs; be defensive
        acquired = False
    try:
        yield acquired
    finally:
        if acquired:
            try:
                released = release_advisory_lock(db, lock_key=key)
                if not released:
                    logger.warning("Advisory lock release reported False for job '%s' key=%s", job_name, key, exc_info=False)
            except Exception as e:
                # log but do not raise
                logger.warning("Failed to release advisory lock for job '%s' key=%s: %s", job_name, key, str(e), exc_info=False)
