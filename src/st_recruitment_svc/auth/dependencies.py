from typing import Set
import logging

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from st_recruitment_svc.models.auth import User
from st_recruitment_svc.models.base import get_db
from st_recruitment_svc.auth.jwt import decode_and_verify_token

logger = logging.getLogger(__name__)


def get_current_user(db: Session = Depends(get_db), authorization: str = Header(None)) -> User:
    """FastAPI dependency to retrieve the current authenticated user and enforce not suspended."""
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization header")
    token = authorization.split(" ", 1)[1]
    payload = decode_and_verify_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        stmt = select(User).where(User.id == int(user_id))
        user = db.execute(stmt).scalars().first()
    except Exception as e:
        logger.error("DB error loading user", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error")
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    # The User model uses status column with values 'active' or 'suspended'
    if getattr(user, "status", None) == "suspended":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account suspended")
    return user


def require_roles(allowed_roles: Set[str]):
    """Factory returning a dependency that enforces the current user's role is allowed."""
    def _dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user

    return _dependency
