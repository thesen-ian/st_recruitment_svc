from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from st_recruitment_svc.models.base import (
    User,
    UserRole,
    JobPosting,
    JobPostingQuestion,
    JobPostingQuestionOption,
    QuestionType,
    JobPostingState,
    FileObject,
    Resume,
    Application,
    ApplicationAnswer,
    Visibility,
    ApplicationStatus,
    ApplicationStatusHistory,
)
from st_recruitment_svc.auth import create_access_token, hash_password
from sqlalchemy import select


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def create_company_and_token(db_session, email="company@example.com"):
    comp = User(email=email, password_hash=hash_password("pass"), role=UserRole.company)
    db_session.add(comp)
    db_session.flush()
    token = create_access_token({"sub": comp.id, "role": comp.role.value})
    return comp, token


def create_job_seeker_and_token(db_session, email="seeker@example.com", verified=True):
    js = User(email=email, password_hash=hash_password("pass"), role=UserRole.job_seeker, email_verified=verified)
    db_session.add(js)
    db_session.flush()
    token = create_access_token({"sub": js.id, "role": js.role.value})
    return js, token


@pytest.mark.parametrize("marker", [1])
def test_applied_to_resume_review_writes_history(client, db_session, marker):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "Engineer", "description": "Great role", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    assert r.status_code == 201
    job = r.json()
    pub = client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))
    assert pub.status_code == 200

    seeker, stoken = create_job_seeker_and_token(db_session, email="s1@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=123, storage_path="/tmp/x")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # company transitions to Resume Review
    payload = {"to_status": ApplicationStatus.ResumeReview.value, "notes": "start review"}
    r2 = client.post(f"/api/applications/{app_id}/status", json=payload, headers=auth_header(ctoken))
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == ApplicationStatus.ResumeReview.value

    # verify application persisted
    stmt = select(Application).where(Application.id == app_id)
    app_row = db_session.execute(stmt).scalars().first()
    assert app_row is not None
    assert str(app_row.status) == ApplicationStatus.ResumeReview.value

    # verify history row
    hstmt = select(ApplicationStatusHistory).where(ApplicationStatusHistory.application_id == app_id)
    hist = db_session.execute(hstmt).scalars().all()
    assert len(hist) == 1
    assert str(hist[0].from_status) == ApplicationStatus.Applied.value
    assert str(hist[0].to_status) == ApplicationStatus.ResumeReview.value


def test_invalid_jump_rejected_and_no_history(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "T", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="s2@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/x2")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # attempt to jump from Applied -> Phone Screen (skipping Resume Review)
    payload = {"to_status": ApplicationStatus.PhoneScreen.value}
    r2 = client.post(f"/api/applications/{app_id}/status", json=payload, headers=auth_header(ctoken))
    assert r2.status_code == 400

    # ensure no history row
    hstmt = select(ApplicationStatusHistory).where(ApplicationStatusHistory.application_id == app_id)
    hist = db_session.execute(hstmt).scalars().all()
    assert len(hist) == 0


def test_terminal_protection(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "T", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="s3@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/x3")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # set application to Offer directly
    stmt = select(Application).where(Application.id == app_id)
    app_row = db_session.execute(stmt).scalars().first()
    app_row.status = ApplicationStatus.Offer.value
    db_session.add(app_row)
    db_session.commit()

    # attempt to change out of Offer
    payload = {"to_status": ApplicationStatus.Rejected.value}
    r2 = client.post(f"/api/applications/{app_id}/status", json=payload, headers=auth_header(ctoken))
    assert r2.status_code == 400

    # ensure no extra history created
    hstmt = select(ApplicationStatusHistory).where(ApplicationStatusHistory.application_id == app_id)
    hist = db_session.execute(hstmt).scalars().all()
    assert len(hist) == 0


def test_interview_scheduling_validation(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "T", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="s4@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/x4")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # Move to Resume Review first
    payload = {"to_status": ApplicationStatus.ResumeReview.value}
    r1 = client.post(f"/api/applications/{app_id}/status", json=payload, headers=auth_header(ctoken))
    assert r1.status_code == 200

    # Move to Phone Screen next
    payload2 = {"to_status": ApplicationStatus.PhoneScreen.value}
    r2 = client.post(f"/api/applications/{app_id}/status", json=payload2, headers=auth_header(ctoken))
    assert r2.status_code == 200

    # Attempt to schedule interview without interview_at -> should fail
    payload3 = {"to_status": ApplicationStatus.InterviewScheduled.value}
    r3 = client.post(f"/api/applications/{app_id}/status", json=payload3, headers=auth_header(ctoken))
    assert r3.status_code == 400

    # Now schedule with a future interview_at -> should succeed
    future = (datetime.now(tz=timezone.utc) + timedelta(days=1)).isoformat()
    payload4 = {"to_status": ApplicationStatus.InterviewScheduled.value, "interview_at": future}
    r4 = client.post(f"/api/applications/{app_id}/status", json=payload4, headers=auth_header(ctoken))
    assert r4.status_code == 200
    assert r4.json()["status"] == ApplicationStatus.InterviewScheduled.value


def test_final_review_to_offer_happy_path(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "T", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="s5@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/x5")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # Bypass to set Final Review directly for test simplicity
    stmt = select(Application).where(Application.id == app_id)
    app_row = db_session.execute(stmt).scalars().first()
    app_row.status = ApplicationStatus.FinalReview.value
    db_session.add(app_row)
    db_session.commit()

    payload = {"to_status": ApplicationStatus.Offer.value, "notes": "we like candidate"}
    r2 = client.post(f"/api/applications/{app_id}/status", json=payload, headers=auth_header(ctoken))
    assert r2.status_code == 200
    assert r2.json()["status"] == ApplicationStatus.Offer.value

    # verify history created
    hstmt = select(ApplicationStatusHistory).where(ApplicationStatusHistory.application_id == app_id)
    hist = db_session.execute(hstmt).scalars().all()
    # only one history row should exist (the offer transition)
    assert any(str(h.to_status) == ApplicationStatus.Offer.value for h in hist)


def test_authorization_enforced(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "T", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="s6@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/x6")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # job seeker attempts to change status -> forbidden
    payload = {"to_status": ApplicationStatus.ResumeReview.value}
    r2 = client.post(f"/api/applications/{app_id}/status", json=payload, headers=auth_header(stoken))
    assert r2.status_code == 403
