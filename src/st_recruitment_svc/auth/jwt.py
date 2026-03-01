from datetime import datetime, timedelta
import logging
from typing import Any, Dict, Optional

import jwt
from fastapi import HTTPException, status

from st_recruitment_svc.config import JWT_SECRET_KEY, JWT_ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES

logger = logging.getLogger(__name__)


def create_access_token(subject_user_id: Any, role: str, extra_claims: Optional[Dict[str, Any]] = None) -> str:
    """Create a JWT access token including standard claims and role."""
    now = datetime.utcnow()
    exp = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: Dict[str, Any] = {
        "sub": str(subject_user_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    # PyJWT returns str in modern versions
    return token


def decode_and_verify_token(token: str) -> Dict[str, Any]:
    """Decode and verify JWT. Raises HTTPException on error suitable for FastAPI endpoints."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError as e:
        logger.error("Expired token", exc_info=True)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.InvalidTokenError as e:
        logger.error("Invalid token", exc_info=True)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
