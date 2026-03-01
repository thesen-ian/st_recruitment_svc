from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from st_recruitment_svc.models.auth import AuthRateLimitCounter

logger = logging.getLogger(__name__)

# Action constants to avoid typos in callers
AUTH_LOGIN = "auth_login"
AUTH_VERIFY_EMAIL = "auth_verify_email"
AUTH_PASSWORD_RESET_REQUEST = "auth_password_reset_request"


def _now_utc() -> datetime:
    """Centralized current time (UTC, tz-aware) to make tests easier to reason about."""
    return datetime.now(timezone.utc)


def check_rate_limit(db: Session, *, identifier: str, action: str, max_attempts: int = 5, window_seconds: int = 900) -> None:
    """Raise 429 if identifier/action has reached max_attempts within active window.

    Semantics: fixed window starting at stored bucket_start. If no active bucket row exists,
    the call is allowed. This function locks the matching row when present to provide
    a consistent view against concurrent increments.

    Caller should call increment_rate_limit only on failed attempts.
    """
    if not identifier:
        # Defensive: do not allow empty identifiers
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid identifier")

    try:
        now = _now_utc()
        window_threshold = now - timedelta(seconds=window_seconds)

        stmt = (
            select(AuthRateLimitCounter)
            .where(
                AuthRateLimitCounter.identifier == identifier,
                AuthRateLimitCounter.action == action,
                AuthRateLimitCounter.bucket_start >= window_threshold,
            )
            .with_for_update()
        )
        result = db.execute(stmt).scalars().first()

        if result is None:
            # No active bucket -> allowed
            return

        if result.count >= max_attempts:
            # Block with generic message that does not leak sensitive details
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please try again later.",
            )

        return
    except HTTPException:
        # Re-raise known HTTP exceptions
        raise
    except Exception:
        logger.error("Failed to check rate limit", exc_info=True)
        # Conservative behavior: when check fails due to internal error, allow the request
        return


def increment_rate_limit(db: Session, *, identifier: str, action: str, window_seconds: int = 900) -> None:
    """Record a failed attempt for given identifier/action.

    Behavior:
    - If an active bucket exists (bucket_start within window_seconds): increment count.
    - Else: create a new bucket starting now with count=1.

    Uses SELECT ... FOR UPDATE to avoid lost updates under concurrent increments.
    Does not commit or rollback the session; transaction lifecycle is managed by caller/get_db.
    """
    if not identifier:
        raise ValueError("identifier must be provided")

    try:
        now = _now_utc()
        window_threshold = now - timedelta(seconds=window_seconds)

        stmt = (
            select(AuthRateLimitCounter)
            .where(
                AuthRateLimitCounter.identifier == identifier,
                AuthRateLimitCounter.action == action,
                AuthRateLimitCounter.bucket_start >= window_threshold,
            )
            .with_for_update()
        )

        row = db.execute(stmt).scalars().first()

        if row is None:
            # No active bucket: create new
            new_row = AuthRateLimitCounter(
                identifier=identifier,
                action=action,
                bucket_start=now,
                count=1,
            )
            db.add(new_row)
            # Do not commit here; let caller manage transaction boundaries
            return

        # Active bucket exists inside window: increment count
        row.count = row.count + 1

        db.add(row)
        # Do not commit here; caller/get_db will handle transaction lifecycle
        return

    except Exception:
        logger.error("Failed to increment rate limit", exc_info=True)
        # On unexpected DB error, raise so caller can handle/record attempt differently
        raise
