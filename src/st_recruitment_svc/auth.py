from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Collection, Dict, Callable

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from st_recruitment_svc.config import JWT_ACCESS_TOKEN_EXPIRES_MINUTES, JWT_ALGORITHM, JWT_SECRET_KEY
from st_recruitment_svc.models.base import User, UserRole, UserStatus, get_db

logger = logging.getLogger(__name__)

# Security scheme to extract Bearer token from Authorization header
_bearer_scheme = HTTPBearer(auto_error=False)


# Password utilities
def hash_password(plain: str) -> str:
    """Hash a plaintext password using bcrypt and return utf-8 string."""
    try:
        hashed = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt())
        return hashed.decode("utf-8")
    except Exception:
        logger.error("Failed to hash password", exc_info=True)
        raise


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        logger.error("Failed to verify password", exc_info=True)
        return False


# JWT utilities
def _get_expiry_timestamp(expires_delta: timedelta | None) -> int:
    now = datetime.now(tz=timezone.utc)
    if expires_delta is None:
        expires_delta = timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRES_MINUTES)
    exp = now + expires_delta
    return int(exp.timestamp())


def create_access_token(payload: Dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Create JWT access token with configured algorithm and secret.

    payload should include at least sub and role claims per project requirements.
    """
    try:
        if not JWT_SECRET_KEY:
            raise RuntimeError("JWT_SECRET_KEY is not configured")

        to_encode = dict(payload)
        to_encode.setdefault("exp", _get_expiry_timestamp(expires_delta))
        token = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
        return token
    except Exception:
        logger.error("Failed to create access token", exc_info=True)
        raise


def decode_access_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT. Raises HTTPException(401) on failure."""
    try:
        if not JWT_SECRET_KEY:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

        # Explicitly provide algorithms to avoid none algorithm attacks
        data = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return data
    except jwt.ExpiredSignatureError:
        logger.error("Expired token", exc_info=True)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        logger.error("Invalid token", exc_info=True)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except Exception:
        logger.error("Unexpected error decoding token", exc_info=True)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


# FastAPI dependencies
def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency to return the authenticated User or raise HTTP errors.

    Enforces token validity and user status (suspended/banned -> 403).
    Does not enforce email_verified here by design.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    token = credentials.credentials
    try:
        claims = decode_access_token(token)
    except HTTPException:
        raise

    sub = claims.get("sub")
    if not sub:
        logger.error("Token missing sub claim")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        stmt = select(User).where(User.id == sub)
        result = db.execute(stmt).scalars().first()
        user = result
    except Exception:
        logger.error("Database error loading user", exc_info=True)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # Enforce status rules
    if user.status in (UserStatus.suspended, UserStatus.banned):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is not active")

    return user


def require_roles(allowed_roles: Collection[UserRole]) -> Callable[[User], User]:
    """Factory returning a dependency that enforces role membership.

    Usage: Depends(require_roles({UserRole.admin}))
    """

    def _dependency(current_user: User = Depends(get_current_user)) -> User:
        try:
            if current_user.role not in allowed_roles:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
            return current_user
        except HTTPException:
            raise
        except Exception:
            logger.error("Error enforcing roles", exc_info=True)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    return _dependency


# Convenience role dependencies
def require_admin() -> Callable[..., Any]:
    return require_roles({UserRole.admin})


def require_company() -> Callable[..., Any]:
    return require_roles({UserRole.company})


def require_job_seeker() -> Callable[..., Any]:
    return require_roles({UserRole.job_seeker})
