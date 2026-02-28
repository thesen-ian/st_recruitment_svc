from __future__ import annotations

from datetime import datetime, timezone
import pytest

from st_recruitment_svc.models.base import (
    User,
    UserRole,
    FileObject,
    Visibility,
    Resume,
    Application,
    ApplicationStatus,
    ApplicationStatusHistory,
    JobPosting,
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


@pytest.mark.parametrize("start_status", [
    ApplicationStatus.Applied.value,
    ApplicationStatus.ResumeReview.value,
    ApplicationStatus.PhoneScreen.value,
])
def test_job_seeker_withdraw_happy_path(client, db_session, start_status):
    # create company, job, publish
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "Engineer", "description": "Great role", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    assert r.status_code == 201
    job = r.json()
    pub = client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))
    assert pub.status_code == 200

    seeker, stoken = create_job_seeker_and_token(db_session, email="withdraw_seeker@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=123, storage_path="/tmp/x")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # If we need to set a non-Applied start status, update directly
    if start_status != ApplicationStatus.Applied.value:
        stmt = select(Application).where(Application.id == app_id)
        app_row = db_session.execute(stmt).scalars().first()
        app_row.status = start_status
        db_session.add(app_row)
        db_session.commit()

    # Job seeker withdraws
    r2 = client.post(f"/api/applications/{app_id}/withdraw", headers=auth_header(stoken))
    assert r2.status_code == 200
    body = r2.json()
    assert body["application_id"] == app_id
    assert body["status"] == ApplicationStatus.Rejected.value

    # verify history row
    hstmt = select(ApplicationStatusHistory).where(ApplicationStatusHistory.application_id == app_id)
    hist = db_session.execute(hstmt).scalars().all()
    assert len(hist) == 1
    assert str(hist[0].from_status) == start_status
    assert str(hist[0].to_status) == ApplicationStatus.Rejected.value
    assert hist[0].changed_by_user_id == seeker.id


@pytest.mark.parametrize("bad_status", [
    ApplicationStatus.InterviewScheduled.value,
    ApplicationStatus.FinalReview.value,
    ApplicationStatus.Offer.value,
    ApplicationStatus.Rejected.value,
])
def test_withdraw_ineligible_statuses_rejected(client, db_session, bad_status):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "T", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="seeker_ineligible@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/x2")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # Set to ineligible status
    stmt = select(Application).where(Application.id == app_id)
    app_row = db_session.execute(stmt).scalars().first()
    app_row.status = bad_status
    db_session.add(app_row)
    db_session.commit()

    r2 = client.post(f"/api/applications/{app_id}/withdraw", headers=auth_header(stoken))
    assert r2.status_code == 400

    # ensure no history created
    hstmt = select(ApplicationStatusHistory).where(ApplicationStatusHistory.application_id == app_id)
    hist = db_session.execute(hstmt).scalars().all()
    assert len(hist) == 0


def test_authorization_enforced_non_owner_and_company(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "T", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    owner, o_token = create_job_seeker_and_token(db_session, email="owner@example.com", verified=True)
    other, other_token = create_job_seeker_and_token(db_session, email="other@example.com", verified=True)
    fo = FileObject(owner_user_id=owner.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/x3")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=owner.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(o_token))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # non-owner job seeker attempt
    r2 = client.post(f"/api/applications/{app_id}/withdraw", headers=auth_header(other_token))
    assert r2.status_code == 403

    # company actor attempt (should be forbidden by require_job_seeker)
    r3 = client.post(f"/api/applications/{app_id}/withdraw", headers=auth_header(ctoken))
    assert r3.status_code == 403


def test_duplicate_withdraw_attempt_no_extra_history(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "T", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="dup@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/x4")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # first withdraw -> succeeds
    r1 = client.post(f"/api/applications/{app_id}/withdraw", headers=auth_header(stoken))
    assert r1.status_code == 200

    # second withdraw -> rejected
    r2 = client.post(f"/api/applications/{app_id}/withdraw", headers=auth_header(stoken))
    assert r2.status_code == 400

    # only one history row exists
    hstmt = select(ApplicationStatusHistory).where(ApplicationStatusHistory.application_id == app_id)
    hist = db_session.execute(hstmt).scalars().all()
    assert len(hist) == 1
