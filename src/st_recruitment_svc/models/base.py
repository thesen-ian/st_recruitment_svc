from __future__ import annotations

import enum
import uuid
from typing import Generator, Iterable, Any

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
    CheckConstraint,
    UniqueConstraint,
    Index,
    text as sa_text,
    select,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, scoped_session, sessionmaker, Session

from st_recruitment_svc.config import DATABASE_URL
import logging

logger = logging.getLogger(__name__)

# Base declarative metadata used by alembic env.py
Base = declarative_base()

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

# Use JSONB and TSVECTOR only when the runtime engine is Postgres. Fall back to portable types otherwise.
try:
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB  # type: ignore
        from sqlalchemy.dialects.postgresql import TSVECTOR  # type: ignore

        JSON_TYPE = JSONB
        JSON_SERVER_DEFAULT = sa_text("'[]'::jsonb")
        TSVECTOR_TYPE = TSVECTOR
    else:
        JSON_TYPE = JSON
        JSON_SERVER_DEFAULT = '[]'
        TSVECTOR_TYPE = Text
except Exception:
    # Be defensive: if anything unexpected happens, fall back to portable types
    JSON_TYPE = JSON
    JSON_SERVER_DEFAULT = '[]'
    TSVECTOR_TYPE = Text


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
    # Align enum values with spec: email_verification and password_reset
    email_verification = "email_verification"
    password_reset = "password_reset"
    download = "download"


class Visibility(str, enum.Enum):
    public = "public"
    private = "private"


class FilePurpose(str, enum.Enum):
    resume = "resume"
    company_logo = "company_logo"
    company_cover = "company_cover"
    application_answer = "application_answer"


# Job posting specific enums
class JobStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    closed = "closed"


class JobPostingState(str, enum.Enum):
    draft = "draft"
    active = "active"
    closed = "closed"


class QuestionType(str, enum.Enum):
    text = "text"
    mcq = "mcq"
    file = "file"


# Application-specific enums
class ApplicationStatus(str, enum.Enum):
    Applied = "Applied"
    ResumeReview = "Resume Review"
    PhoneScreen = "Phone Screen"
    InterviewScheduled = "Interview Scheduled"
    InterviewComplete = "Interview Complete"
    FinalReview = "Final Review"
    Offer = "Offer"
    Rejected = "Rejected"

    def __str__(self) -> str:
        # Ensure str(enum_member) yields the human-friendly value used across the DB and API
        return self.value


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
    # updated_at is useful for last-modified semantics
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Company-specific optional metadata (nullable, only used when role==company)
    company_name = Column(String(255), nullable=True)
    business_registration_number = Column(String(255), nullable=True)
    contact_information = Column(JSON, nullable=True)

    tokens = relationship("Token", back_populates="user", cascade="all, delete-orphan")
    file_objects = relationship("FileObject", back_populates="owner", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")
    notification_preferences = relationship("NotificationPreferences", back_populates="user", uselist=False, cascade="all, delete-orphan")
    # One-to-one job seeker profile when user is a job_seeker
    job_seeker_profile = relationship("JobSeekerProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    # One-to-one company profile when user is a company
    company_profile = relationship("CompanyProfile", back_populates="company", uselist=False, cascade="all, delete-orphan")
    # Resumes owned by user
    resumes = relationship("Resume", back_populates="user", cascade="all, delete-orphan")
    # Jobs posted by company users
    jobs = relationship("Job", back_populates="company", cascade="all, delete-orphan")
    # Job postings (more detailed postings with application questions)
    job_postings = relationship("JobPosting", back_populates="company", cascade="all, delete-orphan")


class Token(Base):
    __tablename__ = "tokens"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # token_hash must be unique per spec
    token_hash = Column(String(512), nullable=False, unique=True)
    type = Column(SAEnum(TokenType, name="token_type"), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    # created_at to track issuance time for tokens
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # link to file object for download tokens; nullable to support other token types
    file_object_id = Column(String(36), ForeignKey("file_objects.id", ondelete="CASCADE"), nullable=True, index=True)

    user = relationship("User", back_populates="tokens")
    file_object = relationship("FileObject", back_populates="tokens")

    __table_args__ = (
        Index('idx_tokens_type', 'type'),
        Index('idx_tokens_file_object_id', 'file_object_id'),
        Index('idx_tokens_expires_at', 'expires_at'),
        CheckConstraint('expires_at > created_at', name='chk_tokens_expires_after_created'),
    )


class FileObject(Base):
    __tablename__ = "file_objects"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # remove auto-named index to keep migration index naming deterministic
    owner_user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    visibility = Column(SAEnum(Visibility, name="file_visibility"), nullable=False, server_default=Visibility.private.value)
    purpose = Column(SAEnum(FilePurpose, name="file_purpose"), nullable=False)
    # original filename is optional per spec
    original_filename = Column(String(1024), nullable=True)
    content_type = Column(String(255), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    storage_path = Column(String(2048), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    owner = relationship("User", back_populates="file_objects")
    tokens = relationship("Token", back_populates="file_object")
    # resume referencing this file object (one-to-one due to unique constraint)
    resume = relationship("Resume", back_populates="file_object", uselist=False)

    __table_args__ = (
        Index('idx_file_objects_owner_user_id', 'owner_user_id'),
        UniqueConstraint('storage_path', name='uq_file_objects_storage_path'),
        CheckConstraint('size_bytes >= 0', name='chk_file_objects_size_bytes_nonnegative'),
        Index('idx_file_objects_visibility', 'visibility'),
    )


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


class JobSeekerProfile(Base):
    __tablename__ = "job_seeker_profiles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # explicit index is managed by migration; avoid index=True here to prevent duplicate indexes
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    full_name = Column(Text, nullable=False)
    email = Column(Text, nullable=False)
    phone = Column(Text, nullable=True)
    location = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    experiences = Column(JSON_TYPE, nullable=False, server_default=JSON_SERVER_DEFAULT)
    education = Column(JSON_TYPE, nullable=False, server_default=JSON_SERVER_DEFAULT)
    skills = Column(JSON_TYPE, nullable=False, server_default=JSON_SERVER_DEFAULT)
    languages = Column(JSON_TYPE, nullable=False, server_default=JSON_SERVER_DEFAULT)
    certifications = Column(JSON_TYPE, nullable=False, server_default=JSON_SERVER_DEFAULT)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="job_seeker_profile")

    __table_args__ = (
        UniqueConstraint('user_id', name='uq_job_seeker_profiles_user_id'),
    )


class CompanyProfile(Base):
    __tablename__ = "company_profiles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # company_id references users.id (company user) to follow existing service convention
    company_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    description = Column(Text, nullable=True)
    website_url = Column(Text, nullable=True)
    industry = Column(Text, nullable=True)
    size = Column(Text, nullable=True)
    hq_location = Column(Text, nullable=True)
    logo_file_object_id = Column(String(36), ForeignKey("file_objects.id", ondelete="SET NULL"), nullable=True)
    cover_file_object_id = Column(String(36), ForeignKey("file_objects.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    company = relationship("User", back_populates="company_profile")
    logo_file_object = relationship("FileObject", foreign_keys=[logo_file_object_id])
    cover_file_object = relationship("FileObject", foreign_keys=[cover_file_object_id])

    __table_args__ = (
        UniqueConstraint('company_id', name='uq_company_profiles_company_id'),
        Index('idx_company_profiles_logo_file_object_id', 'logo_file_object_id'),
        Index('idx_company_profiles_cover_file_object_id', 'cover_file_object_id'),
    )


class Resume(Base):
    __tablename__ = "resumes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    label = Column(Text, nullable=False)
    file_object_id = Column(String(36), ForeignKey("file_objects.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # relationships to aid ORM usage and ownership checks
    user = relationship("User", back_populates="resumes")
    file_object = relationship("FileObject", back_populates="resume")

    __table_args__ = (
        Index('idx_resumes_user_id', 'user_id'),
        UniqueConstraint('file_object_id', name='uq_resumes_file_object_id'),
    )


# Job postings model used for public listings. Keep schema minimal and suitable for public consumption.
class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(512), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(SAEnum(JobStatus, name="job_status"), nullable=False, server_default=JobStatus.draft.value)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    company = relationship("User", back_populates="jobs")

    __table_args__ = (
        Index('idx_jobs_company_id', 'company_id'),
        Index('idx_jobs_status', 'status'),
    )


# New JobPosting models (detailed postings + application questions)
class JobPosting(Base):
    __tablename__ = "job_postings"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    state = Column(SAEnum(JobPostingState, name="job_posting_state"), nullable=False, server_default=JobPostingState.draft.value)
    title = Column(String(512), nullable=False)
    description = Column(Text, nullable=False)
    location = Column(Text, nullable=True)
    employment_type = Column(Text, nullable=True)
    seniority = Column(Text, nullable=True)
    salary_min = Column(Integer, nullable=True)
    salary_max = Column(Integer, nullable=True)
    currency = Column(String(3), nullable=True)
    remote_policy = Column(Text, nullable=True)
    deadline = Column(DateTime(timezone=True), nullable=True)

    # Denormalized company_name to be populated by application when persisting postings
    company_name = Column(String(255), nullable=True)
    # search_document stores computed tsvector on Postgres; portable Text on other DBs
    search_document = Column(TSVECTOR_TYPE, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    published_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    company = relationship("User", back_populates="job_postings")
    questions = relationship("JobPostingQuestion", back_populates="job_posting", cascade="all, delete-orphan", order_by="JobPostingQuestion.position")

    __table_args__ = (
        Index('idx_job_postings_company_id', 'company_id'),
        Index('idx_job_postings_state', 'state'),
        CheckConstraint(
            "state IN ('draft','active','closed')",
            name='chk_job_postings_state_allowed'
        ),
    )


class JobPostingQuestion(Base):
    __tablename__ = "job_posting_questions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_posting_id = Column(String(36), ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False)
    type = Column(SAEnum(QuestionType, name="job_posting_question_type"), nullable=False)
    prompt = Column(Text, nullable=False)
    is_required = Column(Boolean, nullable=False, server_default="false")
    position = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    job_posting = relationship("JobPosting", back_populates="questions")
    options = relationship("JobPostingQuestionOption", back_populates="question", cascade="all, delete-orphan", order_by="JobPostingQuestionOption.position")

    __table_args__ = (
        Index('idx_job_posting_questions_job_posting_id', 'job_posting_id'),
        UniqueConstraint('job_posting_id', 'position', name='uq_job_posting_questions_job_posting_id_position'),
    )


class JobPostingQuestionOption(Base):
    __tablename__ = "job_posting_question_options"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    question_id = Column(String(36), ForeignKey("job_posting_questions.id", ondelete="CASCADE"), nullable=False)
    label = Column(Text, nullable=False)
    position = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    question = relationship("JobPostingQuestion", back_populates="options")

    __table_args__ = (
        Index('idx_job_posting_question_options_question_id', 'question_id'),
        UniqueConstraint('question_id', 'position', name='uq_job_posting_question_options_question_id_position'),
    )


# Applications and answers
class Application(Base):
    __tablename__ = "applications"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    job_seeker_user_id = Column(String(36), nullable=False)
    selected_resume_id = Column(String(36), ForeignKey("resumes.id", ondelete="RESTRICT"), nullable=False)
    cover_letter = Column(Text, nullable=True)
    status = Column(SAEnum(ApplicationStatus, name="application_status"), nullable=False, server_default=ApplicationStatus.Applied.value)
    applied_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    answers = relationship("ApplicationAnswer", back_populates="application", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('job_id', 'job_seeker_user_id', name='uq_applications_job_id_job_seeker_user_id'),
        Index('idx_applications_job_seeker_user_id', 'job_seeker_user_id'),
        Index('idx_applications_job_id', 'job_id'),
        Index('idx_applications_status', 'status'),
        Index('idx_applications_job_id_status', 'job_id', 'status'),
    )


class ApplicationAnswer(Base):
    __tablename__ = "application_answers"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    application_id = Column(String(36), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    question_id = Column(String(36), ForeignKey("job_posting_questions.id", ondelete="RESTRICT"), nullable=False)
    answer_text = Column(Text, nullable=True)
    selected_option_id = Column(String(36), ForeignKey("job_posting_question_options.id", ondelete="RESTRICT"), nullable=True)
    file_object_id = Column(String(36), ForeignKey("file_objects.id", ondelete="RESTRICT"), nullable=True)

    application = relationship("Application", back_populates="answers")

    __table_args__ = (
        UniqueConstraint('application_id', 'question_id', name='uq_application_answers_application_id_question_id'),
        Index('idx_application_answers_application_id', 'application_id'),
        Index('idx_application_answers_question_id', 'question_id'),
        CheckConstraint("((answer_text IS NOT NULL) + (selected_option_id IS NOT NULL) + (file_object_id IS NOT NULL)) = 1", name='chk_application_answers_single_answer_field'),
    )


# New models: application status history and company-only application notes
class ApplicationStatusHistory(Base):
    __tablename__ = "application_status_history"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    application_id = Column(String(36), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    from_status = Column(SAEnum(ApplicationStatus, name="application_status"), nullable=True)
    to_status = Column(SAEnum(ApplicationStatus, name="application_status"), nullable=False)
    changed_by_user_id = Column(String(36), nullable=False)
    changed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    notes = Column(Text, nullable=True)

    __table_args__ = (
        # Ensure index sorts by changed_at DESC for newest-first queries
        Index('idx_application_status_history_application_id_changed_at', 'application_id', sa_text('changed_at DESC')),
        Index('idx_application_status_history_application_id', 'application_id'),
    )


class ApplicationNote(Base):
    __tablename__ = "application_notes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    application_id = Column(String(36), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    note_text = Column(Text, nullable=False)
    created_by_user_id = Column(String(36), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        # Ensure index sorts by created_at DESC for newest-first queries
        Index('idx_application_notes_application_id_created_at', 'application_id', sa_text('created_at DESC')),
    )


# Helper to ensure a CompanyProfile exists for a given company user within the same session.
def ensure_company_profile(session: Session, user: User) -> CompanyProfile:
    """Ensure a CompanyProfile exists for user. Idempotent: returns existing profile if present.

    This helper performs a SELECT to detect an existing profile and only adds a new
    CompanyProfile when none is present. It deliberately does not commit or flush
    so callers can coordinate transactional behavior.
    """
    try:
        if user.role != UserRole.company:
            raise ValueError("ensure_company_profile called for non-company user")

        # Check if a profile already exists in the current session or DB
        stmt = select(CompanyProfile).where(CompanyProfile.company_id == user.id)
        existing = session.execute(stmt).scalars().first()
        if existing:
            return existing

        profile = CompanyProfile(company_id=user.id)
        session.add(profile)
        # Do not flush here; allow caller's transaction flow to manage flush/commit
        return profile
    except Exception as e:
        logger.error("Failed to ensure company profile for user %s: %s", getattr(user, 'id', None), e, exc_info=True)
        raise


def create_user(session: Session, email: str, password_hash: str, role: UserRole, **kwargs: Any) -> User:
    """Create a User and associated CompanyProfile when role==company.

    This function performs inserts and flushes within the provided Session so the
    caller can control transaction boundaries (commit/rollback). It is explicit
    and avoids global Session event listeners.
    """
    try:
        user = User(email=email, password_hash=password_hash, role=role, **kwargs)
        session.add(user)
        # Flush to ensure DB-side constraints and assign any defaults
        session.flush()

        if role == UserRole.company:
            # Ensure profile exists; ensure_company_profile is idempotent
            ensure_company_profile(session, user)
            # Flush profile so that it is persisted in the same transaction
            session.flush()

        return user
    except Exception as e:
        logger.error("Failed to create user (email=%s role=%s): %s", email, role, e, exc_info=True)
        raise
