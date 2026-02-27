from __future__ import annotations

import enum
import uuid
from typing import Generator

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
    create_engine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, scoped_session, sessionmaker, Session

from st_recruitment_svc.config import DATABASE_URL

# Base declarative metadata used by alembic env.py
Base = declarative_base()

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def get_db() -> Generator[Session, None, None]:
    session = scoped_session(sessionmaker(bind=engine))
    try:
        yield session
    finally:
        session.close()


# Domain enums
class UserRole(str, enum.Enum):
    job_seeker = "job_seeker"
    company = "company"
    admin = "admin"


class UserStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    banned = "banned"


class TokenType(str, enum.Enum):
    verify = "verify"
    reset = "reset"
    download = "download"


class Visibility(str, enum.Enum):
    public = "public"
    private = "private"


# ORM models
class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # unique creates an index; avoid redundant index=True
    email = Column(String(255), nullable=False, unique=True)
    password_hash = Column(String(512), nullable=False)
    role = Column(SAEnum(UserRole, name="user_role"), nullable=False)
    email_verified = Column(Boolean, nullable=False, server_default="false")
    status = Column(SAEnum(UserStatus, name="user_status"), nullable=False, server_default=UserStatus.active.value)
    failed_login_count = Column(Integer, nullable=False, server_default="0")
    locked_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    tokens = relationship("Token", back_populates="user", cascade="all, delete-orphan")
    file_objects = relationship("FileObject", back_populates="owner", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")
    notification_preferences = relationship("NotificationPreferences", back_populates="user", uselist=False, cascade="all, delete-orphan")


class Token(Base):
    __tablename__ = "tokens"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(512), nullable=False)
    type = Column(SAEnum(TokenType, name="token_type"), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="tokens")


class FileObject(Base):
    __tablename__ = "file_objects"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    visibility = Column(SAEnum(Visibility, name="file_visibility"), nullable=False, server_default=Visibility.private.value)
    purpose = Column(String(255), nullable=False)
    original_filename = Column(String(1024), nullable=False)
    content_type = Column(String(255), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    storage_path = Column(String(2048), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    owner = relationship("User", back_populates="file_objects")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(255), nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    related_entity_type = Column(String(255), nullable=True)
    related_entity_id = Column(String(255), nullable=True)
    is_read = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="notifications")


class NotificationPreferences(Base):
    __tablename__ = "notification_preferences"

    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    email_enabled = Column(Boolean, nullable=False, server_default="true")
    sms_enabled = Column(Boolean, nullable=False, server_default="false")
    push_enabled = Column(Boolean, nullable=False, server_default="true")
    per_event_settings_json = Column(JSON, nullable=False, server_default='{}')

    user = relationship("User", back_populates="notification_preferences")
