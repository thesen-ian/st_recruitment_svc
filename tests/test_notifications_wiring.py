from __future__ import annotations

from datetime import datetime, timezone, timedelta
import pytest

from st_recruitment_svc.models.base import (
    User,
    UserRole,
    FileObject,
    Resume,
    Notification,
    NotificationPreferences,
)
from st_recruitment_svc.auth import create_access_token, hash_password
from sqlalchemy import select


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def create_company_and_token(db_session, email="company-notif@example.com"):
    comp = User(email=email, password_hash=hash_password("pass"), role=UserRole.company)
    db_session.add(comp)
    db_session.flush()
    token = create_access_token({"sub": comp.id, "role": comp.role.value})
    return comp, token


def create_job_seeker_and_token(db_session, email="seeker-notif@example.com", verified=True):
    js = User(email=email, password_hash=hash_password("pass"), role=UserRole.job_seeker, email_verified=verified)
    db_session.add(js)
    db_session.flush()
    token = create_access_token({"sub": js.id, "role": js.role.value})
    return js, token


@pytest.mark.parametrize("marker", [1])
def test_application_submission_creates_notifications(client, db_session, marker):
    # Setup company and job
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "Notif Role", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    assert r.status_code == 201
    job = r.json()
    # publish
    pub = client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))
    assert pub.status_code == 200

    # create seeker and resume
    seeker, stoken = create_job_seeker_and_token(db_session, email="notif-seeker@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility="private", purpose="resume", content_type="application/pdf", size_bytes=123, storage_path="/tmp/x")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    # apply
    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # Notifications should exist for company and seeker
    stmt_c = select(Notification).where(Notification.user_id == company.id, Notification.type == "application_submitted")
    rows_c = db_session.execute(stmt_c).scalars().all()
    assert len(rows_c) >= 1

    stmt_s = select(Notification).where(Notification.user_id == seeker.id, Notification.type == "application_submitted")
    rows_s = db_session.execute(stmt_s).scalars().all()
    assert len(rows_s) >= 1

    # Validate related_entity mapping
    assert any(getattr(n, "related_entity_type", None) == "application" and getattr(n, "related_entity_id", None) == app_id for n in rows_c)
    assert any(getattr(n, "related_entity_type", None) == "application" and getattr(n, "related_entity_id", None) == app_id for n in rows_s)


def test_status_change_respects_preferences(client, db_session):
    # Setup company, job, seeker
    company, ctoken = create_company_and_token(db_session, email="company-status@example.com")
    payload = {"title": "Status Role", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="status-seeker@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility="private", purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/sx")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # Create preferences disabling notify_application_status_changed for seeker
    pref = NotificationPreferences(user_id=seeker.id, in_app_enabled=True, notify_application_status_changed=False)
    db_session.add(pref)
    db_session.commit()

    # Company transitions status
    payload2 = {"to_status": "Resume Review", "notes": "start review"}
    r2 = client.post(f"/api/applications/{app_id}/status", json=payload2, headers=auth_header(ctoken))
    assert r2.status_code == 200

    # Candidate should not receive application_status_changed
    stmt_s = select(Notification).where(Notification.user_id == seeker.id, Notification.type == "application_status_changed")
    rows_s = db_session.execute(stmt_s).scalars().all()
    assert len(rows_s) == 0

    # Company should have a notification
    stmt_c = select(Notification).where(Notification.user_id == company.id, Notification.type == "application_status_changed")
    rows_c = db_session.execute(stmt_c).scalars().all()
    assert len(rows_c) >= 1


def test_withdrawal_creates_notifications(client, db_session):
    company, ctoken = create_company_and_token(db_session, email="company-withdraw@example.com")
    payload = {"title": "Withdraw Role", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="withdraw-seeker@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility="private", purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/wx")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # Withdraw
    r2 = client.post(f"/api/applications/{app_id}/withdraw", headers=auth_header(stoken))
    assert r2.status_code == 200

    # Both company and seeker should have application_withdrawn notifications
    stmt_c = select(Notification).where(Notification.user_id == company.id, Notification.type == "application_withdrawn")
    rows_c = db_session.execute(stmt_c).scalars().all()
    assert len(rows_c) >= 1

    stmt_s = select(Notification).where(Notification.user_id == seeker.id, Notification.type == "application_withdrawn")
    rows_s = db_session.execute(stmt_s).scalars().all()
    assert len(rows_s) >= 1


def test_interview_proposal_creates_notification_for_candidate(client, db_session):
    # Setup basic entities
    # Reuse helper from interviews tests style
    company = User(email="i-company@example.com", password_hash=hash_password("x"), role=UserRole.company)
    job_seeker = User(email="i-seeker@example.com", password_hash=hash_password("y"), role=UserRole.job_seeker, email_verified=True)
    db_session.add_all([company, job_seeker])
    db_session.flush()

    # create job and application
    r = client.post("/api/jobs", json={"title": "I Role", "description": "D", "questions": []}, headers=auth_header(create_access_token({"sub": company.id, "role": company.role.value})))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(create_access_token({"sub": company.id, "role": company.role.value})))

    fo = FileObject(owner_user_id=job_seeker.id, visibility="private", purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/ix")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=job_seeker.id, label="R", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    # Apply
    stoken = create_access_token({"sub": job_seeker.id, "role": job_seeker.role.value})
    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # Company proposes interview
    ctoken = create_access_token({"sub": company.id, "role": company.role.value})
    start = (datetime.now(tz=timezone.utc) + timedelta(days=1)).isoformat()
    payload = {"start_at": start, "duration_minutes": 30, "format": "video", "location_or_link": "https://meet", "interviewer_names": ["A"]}
    resp = client.post(f"/api/applications/{app_id}/interview/propose", json=payload, headers=auth_header(ctoken))
    assert resp.status_code == 201
    interview_id = resp.json()["interview_id"]

    # Candidate should have an interview notification immediately
    stmt_s = select(Notification).where(Notification.user_id == job_seeker.id, Notification.related_entity_type == "interview", Notification.related_entity_id == interview_id)
    rows_s = db_session.execute(stmt_s).scalars().all()
    assert len(rows_s) >= 1
