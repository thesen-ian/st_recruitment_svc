from __future__ import annotations

from datetime import timedelta
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from st_recruitment_svc.auth import create_access_token
from st_recruitment_svc.models.base import User, UserRole, UserStatus
from st_recruitment_svc.services.admin_audit import write_admin_audit
from st_recruitment_svc.models.admin import AdminAuditLog


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_admin_router_access_control(client, db_session):
    # Create users: normal active, admin active, admin suspended
    normal = User(email="normal@example.com", password_hash="x", role=UserRole.job_seeker, status=UserStatus.active)
    admin = User(email="admin@example.com", password_hash="x", role=UserRole.admin, status=UserStatus.active)
    admin_suspended = User(email="adm_suspend@example.com", password_hash="x", role=UserRole.admin, status=UserStatus.suspended)

    db_session.add_all([normal, admin, admin_suspended])
    db_session.commit()

    token_none = None
    token_normal = create_access_token({"sub": normal.id, "role": normal.role.value})
    token_admin = create_access_token({"sub": admin.id, "role": admin.role.value})
    token_admin_s = create_access_token({"sub": admin_suspended.id, "role": admin_suspended.role.value})

    # Unauthenticated should receive 401
    r = client.get("/api/admin/ping")
    assert r.status_code == 401

    # Authenticated non-admin should receive 403
    r = client.get("/api/admin/ping", headers=_auth_header(token_normal))
    assert r.status_code == 403

    # Authenticated admin should succeed
    r = client.get("/api/admin/ping", headers=_auth_header(token_admin))
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    # Suspended admin must be rejected (403)
    r = client.get("/api/admin/ping", headers=_auth_header(token_admin_s))
    assert r.status_code == 403


def test_write_admin_audit_creates_row(db_session):
    # Create an admin user
    admin = User(email="audit_admin@example.com", password_hash="x", role=UserRole.admin, status=UserStatus.active)
    db_session.add(admin)
    db_session.commit()

    details = {"reason": "test action", "extra": 123}
    audit = write_admin_audit(db_session, admin_user_id=admin.id, action_type="TEST_ACTION", target_type="job", target_id="job-1", details_json=details)

    # After flush, the audit should have an id and be queryable in same session
    assert audit.id is not None
    # Query back using select() API
    stmt = select(AdminAuditLog).where(AdminAuditLog.id == audit.id)
    found = db_session.execute(stmt).scalars().one()
    assert found.admin_user_id == admin.id
    assert found.action_type == "TEST_ACTION"
    assert found.target_type == "job"
    assert found.target_id == "job-1"
    assert found.details_json == details
