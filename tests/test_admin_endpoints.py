from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy import select
import pytest

from st_recruitment_svc.auth import create_access_token
from st_recruitment_svc.models.base import User, UserRole, UserStatus, Job, JobStatus, Application
from st_recruitment_svc.models.admin import AdminAuditLog


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_metrics_access_control_and_content(client, db_session):
    # Setup: create admin and normal user
    admin = User(email="adm@example.com", password_hash="x", role=UserRole.admin, status=UserStatus.active)
    normal = User(email="u1@example.com", password_hash="x", role=UserRole.job_seeker, status=UserStatus.active)
    db_session.add_all([admin, normal])
    # Ensure users have ids assigned before referencing them
    db_session.flush()

    # Create job (published and not removed)
    job = Job(company_id=admin.id, title="Job 1", status=JobStatus.published, is_removed=False)
    db_session.add(job)
    # Flush so job.id is populated before creating dependent Application
    db_session.flush()

    # Create application
    app = Application(job_id=job.id, job_seeker_user_id=normal.id, selected_resume_id="none")
    db_session.add(app)

    db_session.commit()

    token_admin = create_access_token({"sub": admin.id, "role": admin.role.value})
    token_normal = create_access_token({"sub": normal.id, "role": normal.role.value})

    # Unauthenticated
    r = client.get("/api/admin/metrics")
    assert r.status_code == 401

    # Non-admin
    r = client.get("/api/admin/metrics", headers=_auth_header(token_normal))
    assert r.status_code == 403

    # Admin
    r = client.get("/api/admin/metrics", headers=_auth_header(token_admin))
    assert r.status_code == 200
    payload = r.json()
    assert "total_users_by_role" in payload
    assert "active_jobs" in payload
    assert payload["active_jobs"] == 1
    assert "total_applications" in payload
    assert payload["total_applications"] >= 1
    assert "recent_activity_summary" in payload

    # Audit log for metrics view exists
    stmt = select(AdminAuditLog).where(AdminAuditLog.action_type == "METRICS_VIEW")
    found = db_session.execute(stmt).scalars().all()
    assert len(found) >= 1


def test_users_list_and_filters_and_audit(client, db_session):
    admin = User(email="adm2@example.com", password_hash="x", role=UserRole.admin, status=UserStatus.active)
    user_a = User(email="alice@example.com", password_hash="x", role=UserRole.job_seeker, status=UserStatus.active)
    user_b = User(email="bob@example.com", password_hash="x", role=UserRole.company, status=UserStatus.suspended, company_name="BobCo")
    db_session.add_all([admin, user_a, user_b])
    db_session.commit()

    token_admin = create_access_token({"sub": admin.id, "role": admin.role.value})

    # list all
    r = client.get("/api/admin/users", headers=_auth_header(token_admin))
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 2
    assert any(item["email"] == "alice@example.com" for item in data["items"])

    # filter by role
    r = client.get("/api/admin/users?role=company", headers=_auth_header(token_admin))
    assert r.status_code == 200
    data = r.json()
    assert all(item["role"] == "company" for item in data["items"])

    # search q by email
    r = client.get("/api/admin/users?q=alice", headers=_auth_header(token_admin))
    assert r.status_code == 200
    data = r.json()
    assert any("alice@example.com" in (item.get("email") or "") for item in data["items"])

    # Audit log for users list exists
    stmt = select(AdminAuditLog).where(AdminAuditLog.action_type == "USERS_LIST")
    found = db_session.execute(stmt).scalars().all()
    assert len(found) >= 1


def test_suspend_and_ban_and_blocked_access_and_audit(client, db_session):
    admin = User(email="admin3@example.com", password_hash="x", role=UserRole.admin, status=UserStatus.active)
    target = User(email="tgt@example.com", password_hash="x", role=UserRole.job_seeker, status=UserStatus.active)
    db_session.add_all([admin, target])
    db_session.commit()

    token_admin = create_access_token({"sub": admin.id, "role": admin.role.value})
    token_target = create_access_token({"sub": target.id, "role": target.role.value})

    # Suspend
    r = client.post(f"/api/admin/users/{target.id}/suspend", json={"reason": "violation"}, headers=_auth_header(token_admin))
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "suspended"

    # Audit exists for USER_SUSPEND
    stmt = select(AdminAuditLog).where(AdminAuditLog.action_type == "USER_SUSPEND", AdminAuditLog.target_id == target.id)
    found = db_session.execute(stmt).scalars().all()
    assert len(found) >= 1
    assert found[-1].details_json.get("reason") == "violation"

    # Suspended user should be blocked from endpoints requiring auth
    r = client.post(f"/api/files/private/{target.id}/download-token", headers=_auth_header(token_target))
    assert r.status_code == 403

    # Idempotent suspend: call again
    r = client.post(f"/api/admin/users/{target.id}/suspend", json={"reason": "second"}, headers=_auth_header(token_admin))
    assert r.status_code == 200

    # Ban (use another user to ban)
    # First create a fresh target2
    target2 = User(email="tgt2@example.com", password_hash="x", role=UserRole.job_seeker, status=UserStatus.active)
    db_session.add(target2)
    db_session.commit()
    token_target2 = create_access_token({"sub": target2.id, "role": target2.role.value})

    r = client.post(f"/api/admin/users/{target2.id}/ban", json={"reason": "severe"}, headers=_auth_header(token_admin))
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "banned"

    stmt = select(AdminAuditLog).where(AdminAuditLog.action_type == "USER_BAN", AdminAuditLog.target_id == target2.id)
    found = db_session.execute(stmt).scalars().all()
    assert len(found) >= 1
    assert found[-1].details_json.get("reason") == "severe"

    # Banned user blocked
    r = client.post(f"/api/files/private/{target2.id}/download-token", headers=_auth_header(token_target2))
    assert r.status_code == 403


# New tests for admin jobs listing

def test_admin_jobs_list_access_control_and_filters_and_audit(client, db_session):
    admin = User(email="jobs_admin@example.com", password_hash="x", role=UserRole.admin, status=UserStatus.active)
    normal = User(email="normal2@example.com", password_hash="x", role=UserRole.job_seeker, status=UserStatus.active)
    db_session.add_all([admin, normal])
    db_session.flush()

    # Create two jobs: one removed, one active
    job_active = Job(company_id=admin.id, title="Active Job", status=JobStatus.published, is_removed=False)
    job_removed = Job(company_id=admin.id, title="Removed Job", status=JobStatus.published, is_removed=True)
    db_session.add_all([job_active, job_removed])
    db_session.commit()

    token_admin = create_access_token({"sub": admin.id, "role": admin.role.value})
    token_normal = create_access_token({"sub": normal.id, "role": normal.role.value})

    # Unauthenticated -> 401
    r = client.get("/api/admin/jobs")
    assert r.status_code == 401

    # Non-admin -> 403
    r = client.get("/api/admin/jobs", headers=_auth_header(token_normal))
    assert r.status_code == 403

    # Admin -> list all
    r = client.get("/api/admin/jobs", headers=_auth_header(token_admin))
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 2
    titles = [it["title"] for it in data["items"]]
    assert "Active Job" in titles and "Removed Job" in titles

    # Filter removed=true
    r = client.get("/api/admin/jobs?removed=true", headers=_auth_header(token_admin))
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    assert all(it["is_removed"] for it in data["items"])

    # Filter removed=false
    r = client.get("/api/admin/jobs?removed=false", headers=_auth_header(token_admin))
    assert r.status_code == 200
    data = r.json()
    assert all(not it["is_removed"] for it in data["items"])

    # q search by title
    r = client.get("/api/admin/jobs?q=Active", headers=_auth_header(token_admin))
    assert r.status_code == 200
    data = r.json()
    assert any("Active Job" in (it.get("title") or "") for it in data["items"]) 

    # Audit log for jobs list exists
    stmt = select(AdminAuditLog).where(AdminAuditLog.action_type == "JOBS_LIST")
    found = db_session.execute(stmt).scalars().all()
    assert len(found) >= 1


# Tests for admin job removal behavior

def test_admin_job_remove_auth_and_behavior_and_audit(client, db_session):
    admin = User(email="rm_admin@example.com", password_hash="x", role=UserRole.admin, status=UserStatus.active)
    normal = User(email="rm_normal@example.com", password_hash="x", role=UserRole.job_seeker, status=UserStatus.active)
    db_session.add_all([admin, normal])
    db_session.flush()

    job = Job(company_id=admin.id, title="To Remove", status=JobStatus.published, is_removed=False)
    db_session.add(job)
    db_session.commit()

    token_admin = create_access_token({"sub": admin.id, "role": admin.role.value})
    token_normal = create_access_token({"sub": normal.id, "role": normal.role.value})

    # Unauthenticated -> 401
    r = client.post(f"/api/admin/jobs/{job.id}/remove")
    assert r.status_code == 401

    # Non-admin -> 403
    r = client.post(f"/api/admin/jobs/{job.id}/remove", headers=_auth_header(token_normal))
    assert r.status_code == 403

    # Admin -> remove succeeds
    r = client.post(f"/api/admin/jobs/{job.id}/remove", headers=_auth_header(token_admin))
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == job.id
    assert data["is_removed"] is True

    # Verify DB state: expire local session state so we read fresh DB values
    db_session.expire_all()
    stmt = select(Job).where(Job.id == job.id)
    found_job = db_session.execute(stmt).scalars().one()
    assert bool(found_job.is_removed) is True

    # Audit row exists with expected details
    stmt = select(AdminAuditLog).where(AdminAuditLog.action_type == "JOB_REMOVE", AdminAuditLog.target_id == job.id)
    found = db_session.execute(stmt).scalars().all()
    assert len(found) >= 1
    last = found[-1]
    assert last.details_json.get("previous_removed") in (True, False)
    assert last.details_json.get("new_removed") is True

    # Idempotency: calling again returns 200 and does not flip state
    r2 = client.post(f"/api/admin/jobs/{job.id}/remove", headers=_auth_header(token_admin))
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["is_removed"] is True

    # Ensure fresh read for audits
    db_session.expire_all()
    # Another audit entry should have been created
    found_after = db_session.execute(stmt).scalars().all()
    assert len(found_after) >= 2
    assert found_after[-1].details_json.get("previous_removed") is True
    assert found_after[-1].details_json.get("new_removed") is True


def test_admin_job_remove_404_when_missing(client, db_session):
    admin = User(email="rm_admin2@example.com", password_hash="x", role=UserRole.admin, status=UserStatus.active)
    db_session.add(admin)
    db_session.commit()

    token_admin = create_access_token({"sub": admin.id, "role": admin.role.value})

    r = client.post(f"/api/admin/jobs/non-existent-id/remove", headers=_auth_header(token_admin))
    assert r.status_code == 404
