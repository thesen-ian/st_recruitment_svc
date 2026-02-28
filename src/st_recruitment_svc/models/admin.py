from __future__ import annotations

import uuid
from sqlalchemy import (
    Column,
    String,
    Text,
    DateTime,
    ForeignKey,
    Index,
    Boolean,
    func,
    CheckConstraint,
)

from .base import Base, JSON_TYPE


class Report(Base):
    """Reports raised by users against entities (jobs, users, etc.)."""

    __tablename__ = "reports"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    reported_by_user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    entity_type = Column(String(255), nullable=False)
    entity_id = Column(String(36), nullable=False)
    reason = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    # Use simple string status to keep cross-dialect portability
    status = Column(String(50), nullable=False, server_default="OPEN")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        Index("idx_reports_reported_by_user_id", "reported_by_user_id"),
        Index("idx_reports_entity", "entity_type", "entity_id"),
        Index("idx_reports_status", "status"),
        # Index to support lookups by resolver
        Index("idx_reports_resolved_by", "resolved_by"),
        # If a report is marked RESOLVED, require resolved_at and resolved_by
        CheckConstraint(
            "NOT (status = 'RESOLVED') OR (resolved_at IS NOT NULL AND resolved_by IS NOT NULL)",
            name='chk_reports_resolved_requires_resolver_and_time'
        ),
    )


class AdminAuditLog(Base):
    """Audit logs for administrative actions. Stored details_json for richer context."""

    __tablename__ = "admin_audit_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    admin_user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    action_type = Column(String(255), nullable=False)
    target_type = Column(String(255), nullable=False)
    target_id = Column(String(36), nullable=True)
    # Use the service-wide JSON_TYPE to get JSONB on Postgres, JSON on SQLite
    details_json = Column(JSON_TYPE, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_admin_audit_logs_admin_user_id", "admin_user_id"),
        Index("idx_admin_audit_logs_action_type", "action_type"),
        Index("idx_admin_audit_logs_target", "target_type", "target_id"),
        Index("idx_admin_audit_logs_created_at", "created_at"),
    )
