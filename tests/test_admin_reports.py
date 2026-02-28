from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy import select

from st_recruitment_svc.auth import create_access_token
from st_recruitment_svc.models.base import User, UserRole, UserStatus
from st_recruitment_svc.models.admin import AdminAuditLog, Report


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_admin_reports_access_control_and_basic_list(client, db_session):
    admin = User(email="r_admin@example.com", password_hash="x", role=UserRole.admin, status=UserStatus.active)
    normal = User(email="reporter@example.com", password_hash="x", role=UserRole.job_seeker, status=UserStatus.active)
    db_session.add_all([admin, normal])
    db_session.flush()

    now = datetime.now(tz=timezone.utc)
    r1 = Report(reported_by_user_id=normal.id, entity_type="job", entity_id="j1", reason="spam", description="spam job", status="OPEN", created_at=now - timedelta(minutes=2))
    r2 = Report(reported_by_user_id=normal.id, entity_type="user", entity_id="u2", reason="abuse", description="insult", status="RESOLVED", resolved_at=now - timedelta(minutes=1), resolved_by=admin.id, created_at=now - timedelta(minutes=1))
    r3 = Report(reported_by_user_id=admin.id, entity_type="job", entity_id="j3", reason="other", description=None, status="OPEN", created_at=now)
    db_session.add_all([r1, r2, r3])
    db_session.commit()

    token_admin = create_access_token({"sub": admin.id, "role": admin.role.value})
    token_normal = create_access_token({"sub": normal.id, "role": normal.role.value})

    # Unauthenticated -> 401
    r = client.get("/api/admin/reports")
    assert r.status_code == 401

    # Non-admin -> 403
    r = client.get("/api/admin/reports", headers=_auth_header(token_normal))
    assert r.status_code == 403

    # Admin -> 200 and returns items
    r = client.get("/api/admin/reports", headers=_auth_header(token_admin))
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 3
    assert any(it["reason"] == "spam" for it in data["items"]) or any(it["reason"] == "other" for it in data["items"])


def test_admin_reports_filter_by_status_and_audit(db_session, client):
    admin = User(email="r_admin2@example.com", password_hash="x", role=UserRole.admin, status=UserStatus.active)
    reporter = User(email="rep2@example.com", password_hash="x", role=UserRole.job_seeker, status=UserStatus.active)
    db_session.add_all([admin, reporter])
    db_session.flush()

    now = datetime.now(tz=timezone.utc)
    open_r = Report(reported_by_user_id=reporter.id, entity_type="job", entity_id="j-open", reason="spam", status="OPEN", created_at=now)
    resolved_r = Report(reported_by_user_id=reporter.id, entity_type="job", entity_id="j-res", reason="abuse", status="RESOLVED", resolved_at=now, resolved_by=admin.id, created_at=now - timedelta(minutes=1))
    db_session.add_all([open_r, resolved_r])
    db_session.commit()

    token_admin = create_access_token({"sub": admin.id, "role": admin.role.value})

    # filter status=RESOLVED
    r = client.get("/api/admin/reports?status=RESOLVED", headers=_auth_header(token_admin))
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    assert all(it["status"] == "RESOLVED" for it in data["items"]) or any(it["status"] == "RESOLVED" for it in data["items"]) 

    # Audit row created
    stmt = select(AdminAuditLog).where(AdminAuditLog.action_type == "REPORTS_LIST")
    found = db_session.execute(stmt).scalars().all()
    assert len(found) >= 1
    # last audit should contain details keys
    last = found[-1]
    assert isinstance(last.details_json, dict)
    assert last.details_json.get("status") == "RESOLVED"


def test_admin_reports_pagination(client, db_session):
    admin = User(email="r_admin3@example.com", password_hash="x", role=UserRole.admin, status=UserStatus.active)
    reporter = User(email="rep3@example.com", password_hash="x", role=UserRole.job_seeker, status=UserStatus.active)
    db_session.add_all([admin, reporter])
    db_session.flush()

    now = datetime.now(tz=timezone.utc)
    # create 3 reports with increasing created_at so ordering is deterministic
    reps = []
    for i in range(3):
        reps.append(Report(reported_by_user_id=reporter.id, entity_type="job", entity_id=f"j{i}", reason=f"r{i}", status="OPEN", created_at=now + timedelta(seconds=i)))
    db_session.add_all(reps)
    db_session.commit()

    token_admin = create_access_token({"sub": admin.id, "role": admin.role.value})

    # limit=1 offset=0 -> 1 item
    r = client.get("/api/admin/reports?limit=1&offset=0", headers=_auth_header(token_admin))
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 3
    assert len(data["items"]) == 1

    # offset=1 -> next item
    r2 = client.get("/api/admin/reports?limit=1&offset=1", headers=_auth_header(token_admin))
    assert r2.status_code == 200
    data2 = r2.json()
    assert len(data2["items"]) == 1
    # Ensure different items between pages
    assert data["items"][0]["id"] != data2["items"][0]["id"]


# New tests for report resolve endpoint

def test_admin_report_resolve_auth_and_behavior_and_audit(client, db_session):
    admin = User(email="res_admin@example.com", password_hash="x", role=UserRole.admin, status=UserStatus.active)
    reporter = User(email="rep_res@example.com", password_hash="x", role=UserRole.job_seeker, status=UserStatus.active)
    db_session.add_all([admin, reporter])
    db_session.flush()

    report = Report(reported_by_user_id=reporter.id, entity_type="job", entity_id="job-123", reason="spam", status="OPEN")
    db_session.add(report)
    db_session.commit()

    token_admin = create_access_token({"sub": admin.id, "role": admin.role.value})
    token_reporter = create_access_token({"sub": reporter.id, "role": reporter.role.value})

    # Unauthenticated -> 401
    r = client.post(f"/api/admin/reports/{report.id}/resolve")
    assert r.status_code == 401

    # Non-admin -> 403
    r = client.post(f"/api/admin/reports/{report.id}/resolve", headers=_auth_header(token_reporter))
    assert r.status_code == 403

    # Admin -> resolve succeeds
    r = client.post(f"/api/admin/reports/{report.id}/resolve", headers=_auth_header(token_admin))
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == report.id
    assert data["status"] == "RESOLVED"

    # Verify DB state: expire local session state so we read fresh DB values
    db_session.expire_all()
    stmt = select(Report).where(Report.id == report.id)
    found_report = db_session.execute(stmt).scalars().one()
    assert found_report.status == "RESOLVED"
    assert found_report.resolved_by == admin.id
    assert found_report.resolved_at is not None

    # Audit row exists with expected details
    stmt_a = select(AdminAuditLog).where(AdminAuditLog.action_type == "REPORT_RESOLVE", AdminAuditLog.target_id == report.id)
    found = db_session.execute(stmt_a).scalars().all()
    assert len(found) >= 1
    first_audit = found[-1]
    assert first_audit.details_json.get("previous_status") in ("OPEN", "RESOLVED")
    assert first_audit.details_json.get("new_status") == "RESOLVED"

    # Idempotency: calling again returns 200 and does not change resolver fields
    prev_resolved_by = found_report.resolved_by
    prev_resolved_at = found_report.resolved_at

    r2 = client.post(f"/api/admin/reports/{report.id}/resolve", headers=_auth_header(token_admin))
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["status"] == "RESOLVED"

    db_session.expire_all()
    found_report_after = db_session.execute(stmt).scalars().one()
    assert found_report_after.resolved_by == prev_resolved_by
    assert found_report_after.resolved_at == prev_resolved_at

    # Another audit entry should have been created for idempotent call
    found_after = db_session.execute(stmt_a).scalars().all()
    assert len(found_after) >= 2
    assert found_after[-1].details_json.get("previous_status") == "RESOLVED"
    assert found_after[-1].details_json.get("new_status") == "RESOLVED"


def test_admin_report_resolve_404_when_missing(client, db_session):
    admin = User(email="res_admin2@example.com", password_hash="x", role=UserRole.admin, status=UserStatus.active)
    db_session.add(admin)
    db_session.commit()

    token_admin = create_access_token({"sub": admin.id, "role": admin.role.value})

    r = client.post(f"/api/admin/reports/non-existent-id/resolve", headers=_auth_header(token_admin))
    assert r.status_code == 404
