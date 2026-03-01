from datetime import timedelta, datetime, timezone
import hashlib
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, constr
from sqlalchemy import select
from sqlalchemy.orm import Session

from st_recruitment_svc.auth.passwords import hash_password, verify_password
from st_recruitment_svc.auth.jwt import create_access_token
from st_recruitment_svc.auth.rate_limit import (
    AUTH_LOGIN,
    AUTH_VERIFY_EMAIL,
    AUTH_PASSWORD_RESET_REQUEST,
    check_rate_limit,
    increment_rate_limit,
    _now_utc,
)
from st_recruitment_svc.models.auth import (
    User,
    EmailVerificationToken,
    PasswordResetToken,
)
from st_recruitment_svc.models.base import get_db
from st_recruitment_svc.config import PASSWORD_RESET_TOKEN_EXPIRE_MINUTES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth")


# Token generator dependency: injectable for tests to capture raw token values.
def token_generator() -> str:
    return secrets.token_urlsafe(32)


# Schemas
class RegisterSchema(BaseModel):
    email: EmailStr
    password: constr(min_length=6)
    role: str


class LoginSchema(BaseModel):
    email: EmailStr
    password: constr(min_length=1)


class TokenSchema(BaseModel):
    token: str


class EmailSchema(BaseModel):
    email: EmailStr


class PasswordResetConfirmSchema(BaseModel):
    token: str
    new_password: constr(min_length=6)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ensure_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure datetime is timezone-aware in UTC. If naive, assume UTC.
    Keeps comparisons robust regardless of DB storage timezone handling.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterSchema,
    db: Session = Depends(get_db),
    token_raw: str = Depends(token_generator),
):
    email_norm = payload.email.strip().lower()
    if payload.role not in ("job_seeker", "company"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role")

    try:
        stmt = select(User).where(User.email == email_norm)
        existing = db.execute(stmt).scalars().first()
    except Exception:
        logger.error("DB error checking existing user", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error")

    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    pwd_hash = hash_password(payload.password)

    user = User(email=email_norm, password_hash=pwd_hash, role=payload.role, status="active")
    db.add(user)
    db.flush()  # ensure user.id is available

    token_hash = _hash_token(token_raw)
    expires_at = _now_utc() + timedelta(hours=24)
    ev = EmailVerificationToken(user_id=user.id, token_hash=token_hash, expires_at=expires_at)
    db.add(ev)

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.error("DB error creating user", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error")

    # In prod we'd enqueue email; here it's a no-op.
    return {"detail": "Registration successful. Please verify your email."}


@router.post("/login")
def login(payload: LoginSchema, request: Request, db: Session = Depends(get_db)):
    email_norm = payload.email.strip().lower()
    identifier = email_norm or request.client.host

    # check rate limit first
    check_rate_limit(db, identifier=identifier, action=AUTH_LOGIN)

    try:
        stmt = select(User).where(User.email == email_norm)
        user = db.execute(stmt).scalars().first()
    except Exception:
        logger.error("DB error loading user", exc_info=True)
        # conservative: count as failed attempt
        try:
            increment_rate_limit(db, identifier=identifier, action=AUTH_LOGIN)
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error")

    if not user or not verify_password(payload.password, user.password_hash):
        try:
            increment_rate_limit(db, identifier=identifier, action=AUTH_LOGIN)
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if getattr(user, "status", None) == "suspended":
        try:
            increment_rate_limit(db, identifier=identifier, action=AUTH_LOGIN)
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account suspended")

    if user.email_verified_at is None:
        try:
            increment_rate_limit(db, identifier=identifier, action=AUTH_LOGIN)
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email not verified")

    token = create_access_token(subject_user_id=user.id, role=user.role)
    return {"access_token": token, "token_type": "bearer"}


@router.post("/verify-email")
def verify_email(
    payload: TokenSchema, request: Request, db: Session = Depends(get_db)
):
    identifier = request.client.host
    check_rate_limit(db, identifier=identifier, action=AUTH_VERIFY_EMAIL)

    token_hash = _hash_token(payload.token)
    try:
        stmt = select(EmailVerificationToken).where(EmailVerificationToken.token_hash == token_hash)
        token_row = db.execute(stmt).scalars().first()
    except Exception:
        logger.error("DB error loading verification token", exc_info=True)
        try:
            increment_rate_limit(db, identifier=identifier, action=AUTH_VERIFY_EMAIL)
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error")

    now = _now_utc()
    if not token_row:
        try:
            increment_rate_limit(db, identifier=identifier, action=AUTH_VERIFY_EMAIL)
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")

    if token_row.used_at is not None:
        try:
            increment_rate_limit(db, identifier=identifier, action=AUTH_VERIFY_EMAIL)
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token already used")

    token_expires = _ensure_aware(token_row.expires_at)
    if token_expires < now:
        try:
            increment_rate_limit(db, identifier=identifier, action=AUTH_VERIFY_EMAIL)
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token expired")

    try:
        user = db.get(User, token_row.user_id)
        if user and user.email_verified_at is None:
            user.email_verified_at = now
            db.add(user)
        token_row.used_at = now
        db.add(token_row)
        db.commit()
    except Exception:
        db.rollback()
        logger.error("DB error verifying email", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error")

    return {"detail": "Email verified"}


@router.post("/password-reset/request")
def password_reset_request(
    payload: EmailSchema, db: Session = Depends(get_db), token_raw: str = Depends(token_generator)
):
    email_norm = payload.email.strip().lower()
    identifier = email_norm
    check_rate_limit(db, identifier=identifier, action=AUTH_PASSWORD_RESET_REQUEST)

    # Always return generic message to avoid leaking existence
    generic = {"detail": "If an account exists for this email, you will receive password reset instructions."}

    try:
        stmt = select(User).where(User.email == email_norm)
        user = db.execute(stmt).scalars().first()
    except Exception:
        logger.error("DB error loading user for password reset", exc_info=True)
        return generic

    if not user:
        return generic

    token_hash = _hash_token(token_raw)
    expires_at = _now_utc() + timedelta(minutes=PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)
    pr = PasswordResetToken(user_id=user.id, token_hash=token_hash, expires_at=expires_at)
    db.add(pr)
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.error("DB error creating password reset token", exc_info=True)

    # No-op for email enqueue
    return generic


@router.post("/password-reset/confirm")
def password_reset_confirm(payload: PasswordResetConfirmSchema, db: Session = Depends(get_db)):
    token_hash = _hash_token(payload.token)
    try:
        stmt = select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        token_row = db.execute(stmt).scalars().first()
    except Exception:
        logger.error("DB error loading password reset token", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error")

    now = _now_utc()
    if not token_row:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")
    if token_row.used_at is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token already used")
    token_expires = _ensure_aware(token_row.expires_at)
    if token_expires < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token expired")

    try:
        user = db.get(User, token_row.user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")
        user.password_hash = hash_password(payload.new_password)
        token_row.used_at = now
        db.add(user)
        db.add(token_row)
        db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        logger.error("DB error confirming password reset", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server error")

    return {"detail": "Password has been reset"}
