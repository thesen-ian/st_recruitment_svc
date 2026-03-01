import bcrypt
import logging
from typing import Union

logger = logging.getLogger(__name__)


def hash_password(plain: str) -> str:
    """Hash a plaintext password using bcrypt. Caller must ensure non-empty password."""
    if not plain:
        raise ValueError("password must not be empty")
    try:
        hashed = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt())
        return hashed.decode("utf-8")
    except Exception as e:
        logger.error("Failed to hash password", exc_info=True)
        raise


def verify_password(plain: str, password_hash: str) -> bool:
    """Verify a plaintext password against a bcrypt hash. Returns False on any verification error."""
    if not plain:
        raise ValueError("password must not be empty")
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception as e:
        logger.error("Password verification error", exc_info=True)
        return False
