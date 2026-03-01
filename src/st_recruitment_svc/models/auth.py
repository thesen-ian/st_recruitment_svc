from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    CheckConstraint,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from .base import Base


class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True)
    # unique implies an index at the DB level; avoid redundant index=True
    email = Column(String(255), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False)
    email_verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    __table_args__ = (
        CheckConstraint("role IN ('job_seeker','company','admin')", name="ck_user_role"),
        CheckConstraint("status IN ('active','suspended')", name="ck_user_status"),
    )


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_token"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(255), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    used_at = Column(DateTime, nullable=True)

    user = relationship("User")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_token"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(255), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    used_at = Column(DateTime, nullable=True)

    user = relationship("User")


class AuthRateLimitCounter(Base):
    __tablename__ = "auth_rate_limit_counter"

    id = Column(Integer, primary_key=True)
    identifier = Column(String(255), nullable=False)
    action = Column(String(128), nullable=False)
    bucket_start = Column(DateTime, nullable=False)
    count = Column(Integer, nullable=False, server_default=text('0'))
    created_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    __table_args__ = (
        # unique constraint to ensure one row per identifier/action/bucket_start
        UniqueConstraint('identifier', 'action', 'bucket_start', name='uq_rl_identifier_action_bucket'),
        CheckConstraint("count >= 0", name="ck_rate_count_nonnegative"),
    )
