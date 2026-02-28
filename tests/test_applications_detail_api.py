from __future__ import annotations

from datetime import datetime
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
def test_job_seeker_and_company_owner_can_fetch_detail(client, db_session, marker):
    # Setup company and publish job
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "Engineer", "description": "Great role", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    assert r.status_code == 201
    job = r.json()
    pub = client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))
    assert pub.status_code == 200

    # create job seeker and resume
    seeker, stoken = create_job_seeker_and_token(db_session, email="detail-seeker@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=123, storage_path="/tmp/d1")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    # apply
    apply_payload = {"selected_resume_id": resume.id, "answers": []}
    ap = client.post(f"/api/jobs/{job['id']}/apply", json=apply_payload, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # job seeker can fetch
    got = client.get(f"/api/applications/{app_id}", headers=auth_header(stoken))
    assert got.status_code == 200
    body = got.json()
    assert body["application_id"] == app_id
    assert body["job_id"] == job["id"]
    assert body["job_seeker_user_id"] == seeker.id
    assert isinstance(body.get("answers"), list)

    # company owner can fetch
    got2 = client.get(f"/api/applications/{app_id}", headers=auth_header(ctoken))
    assert got2.status_code == 200


def test_unauthorized_users_cannot_fetch_detail(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "Engineer", "description": "Great role", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="owner2@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/dd1")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # another job seeker should be forbidden
    other, other_token = create_job_seeker_and_token(db_session, email="other2@example.com", verified=True)
    got = client.get(f"/api/applications/{app_id}", headers=auth_header(other_token))
    assert got.status_code == 403

    # different company should be forbidden
    other_comp, other_ctoken = create_company_and_token(db_session, email="othercomp@example.com")
    got2 = client.get(f"/api/applications/{app_id}", headers=auth_header(other_ctoken))
    assert got2.status_code == 403


def test_admin_can_fetch_detail_if_admin_role_exists(client, db_session):
    # create admin user
    admin = User(email="admin@example.com", password_hash=hash_password("pass"), role=UserRole.admin)
    db_session.add(admin)
    db_session.flush()
    admin_token = create_access_token({"sub": admin.id, "role": admin.role.value})

    # reuse flow to create an application
    company, ctoken = create_company_and_token(db_session, email="comp-admin@example.com")
    payload = {"title": "Eng", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="aadmin-seeker@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/adminr1")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    got = client.get(f"/api/applications/{app_id}", headers=auth_header(admin_token))
    assert got.status_code == 200


def test_file_answer_exposes_only_file_object_id(client, db_session):
    # job with file question
    company, ctoken = create_company_and_token(db_session, email="filecomp@example.com")
    payload = {"title": "Upload role", "description": "D", "questions": [{"type": "file", "prompt": "Upload CV", "is_required": True}]}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="filedetail-seeker@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="application_answer", content_type="application/pdf", size_bytes=1024, storage_path="/tmp/filedet1")
    db_session.add(fo)
    db_session.flush()
    resume_fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/rfile1")
    db_session.add(resume_fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=resume_fo.id)
    db_session.add(resume)
    db_session.commit()

    job_detail = client.get(f"/api/jobs/{job['id']}").json()
    qid = job_detail['questions'][0]['id']

    payload_apply = {"selected_resume_id": resume.id, "answers": [{"question_id": qid, "file_object_id": fo.id}]}
    resp = client.post(f"/api/jobs/{job['id']}/apply", json=payload_apply, headers=auth_header(stoken))
    assert resp.status_code == 201
    app_id = resp.json()["application_id"]

    got = client.get(f"/api/applications/{app_id}", headers=auth_header(stoken))
    assert got.status_code == 200
    body = got.json()
    answers = body.get("answers", [])
    assert len(answers) == 1
    ans = answers[0]
    assert ans.get("file_object_id") == fo.id
    # ensure no leakage of URLs/tokens
    for forbidden in ("url", "download_url", "token", "signed_url"):
        assert forbidden not in ans


def test_not_found_returns_404(client, db_session):
    seeker, stoken = create_job_seeker_and_token(db_session, email="nf@example.com", verified=True)
    resp = client.get("/api/applications/00000000-0000-0000-0000-000000000000", headers=auth_header(stoken))
    assert resp.status_code == 404
