from __future__ import annotations

from datetime import datetime
import pytest

from st_recruitment_svc.models.base import (
    User,
    UserRole,
    JobPosting,
    QuestionType,
    JobPostingState,
    FileObject,
    Resume,
    Application,
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
def test_job_seeker_list_scoping(client, db_session, marker):
    # Setup company + job
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "Eng", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    assert r.status_code == 201
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    # two seekers
    s1, t1 = create_job_seeker_and_token(db_session, email="s1@example.com", verified=True)
    fo1 = FileObject(owner_user_id=s1.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/r1")
    db_session.add(fo1)
    db_session.flush()
    r1 = Resume(user_id=s1.id, label="CV1", file_object_id=fo1.id)
    db_session.add(r1)

    s2, t2 = create_job_seeker_and_token(db_session, email="s2@example.com", verified=True)
    fo2 = FileObject(owner_user_id=s2.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/r2")
    db_session.add(fo2)
    db_session.flush()
    r2 = Resume(user_id=s2.id, label="CV2", file_object_id=fo2.id)
    db_session.add(r2)
    db_session.commit()

    # both apply
    ap1 = {"selected_resume_id": r1.id, "answers": []}
    ap2 = {"selected_resume_id": r2.id, "answers": []}
    res1 = client.post(f"/api/jobs/{job['id']}/apply", json=ap1, headers=auth_header(t1))
    assert res1.status_code == 201
    res2 = client.post(f"/api/jobs/{job['id']}/apply", json=ap2, headers=auth_header(t2))
    assert res2.status_code == 201

    # as s1, list
    lst = client.get("/api/job-seekers/me/applications", headers=auth_header(t1))
    assert lst.status_code == 200
    body = lst.json()
    assert "items" in body and isinstance(body["items"], list)
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["job_id"] == job["id"]
    # job_seeker_user_id should not be present in job-seeker view
    assert "job_seeker_user_id" not in item


def test_company_list_scoping_and_filters(client, db_session):
    # create two companies and jobs
    comp_a, token_a = create_company_and_token(db_session, email="a@c.com")
    comp_b, token_b = create_company_and_token(db_session, email="b@c.com")

    payload = {"title": "J1", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(token_a))
    job_a1 = r.json()
    client.post(f"/api/jobs/{job_a1['id']}/publish", headers=auth_header(token_a))

    payload2 = {"title": "J2", "description": "D", "questions": []}
    r2 = client.post("/api/jobs", json=payload2, headers=auth_header(token_a))
    job_a2 = r2.json()
    client.post(f"/api/jobs/{job_a2['id']}/publish", headers=auth_header(token_a))

    r3 = client.post("/api/jobs", json={"title": "JB", "description": "D", "questions": []}, headers=auth_header(token_b))
    job_b1 = r3.json()
    client.post(f"/api/jobs/{job_b1['id']}/publish", headers=auth_header(token_b))

    # create seekers and apply: one to a1, one to a2, one to b1
    s1, t1 = create_job_seeker_and_token(db_session, email="sa1@example.com", verified=True)
    fo1 = FileObject(owner_user_id=s1.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/sa1")
    db_session.add(fo1)
    db_session.flush()
    resume1 = Resume(user_id=s1.id, label="CV", file_object_id=fo1.id)
    db_session.add(resume1)

    s2, t2 = create_job_seeker_and_token(db_session, email="sa2@example.com", verified=True)
    fo2 = FileObject(owner_user_id=s2.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/sa2")
    db_session.add(fo2)
    db_session.flush()
    resume2 = Resume(user_id=s2.id, label="CV", file_object_id=fo2.id)
    db_session.add(resume2)

    s3, t3 = create_job_seeker_and_token(db_session, email="sb1@example.com", verified=True)
    fo3 = FileObject(owner_user_id=s3.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/sb1")
    db_session.add(fo3)
    db_session.flush()
    resume3 = Resume(user_id=s3.id, label="CV", file_object_id=fo3.id)
    db_session.add(resume3)
    db_session.commit()

    # apply
    r1 = client.post(f"/api/jobs/{job_a1['id']}/apply", json={"selected_resume_id": resume1.id, "answers": []}, headers=auth_header(t1))
    assert r1.status_code == 201
    r2 = client.post(f"/api/jobs/{job_a2['id']}/apply", json={"selected_resume_id": resume2.id, "answers": []}, headers=auth_header(t2))
    assert r2.status_code == 201
    r3 = client.post(f"/api/jobs/{job_b1['id']}/apply", json={"selected_resume_id": resume3.id, "answers": []}, headers=auth_header(t3))
    assert r3.status_code == 201

    # company A lists: should see only applications to its jobs (a1 and a2)
    lst = client.get("/api/companies/me/applications", headers=auth_header(token_a))
    assert lst.status_code == 200
    body = lst.json()
    ids = {it["job_id"] for it in body["items"]}
    assert job_a1["id"] in ids and job_a2["id"] in ids
    assert job_b1["id"] not in ids

    # filter by job_id = a1
    flt = client.get(f"/api/companies/me/applications?job_id={job_a1['id']}", headers=auth_header(token_a))
    assert flt.status_code == 200
    fbody = flt.json()
    assert len(fbody["items"]) == 1
    assert fbody["items"][0]["job_id"] == job_a1["id"]

    # filter by job_id not owned (job_b1) -> expect 403
    bad = client.get(f"/api/companies/me/applications?job_id={job_b1['id']}", headers=auth_header(token_a))
    assert bad.status_code == 403

    # status filter: Applied present
    st = client.get(f"/api/companies/me/applications?status=Applied", headers=auth_header(token_a))
    assert st.status_code == 200
    # there should be two applied
    assert len(st.json()["items"]) == 2

    # status filter: unknown status -> empty list
    st2 = client.get(f"/api/companies/me/applications?status=UnknownStatus", headers=auth_header(token_a))
    assert st2.status_code == 200
    assert len(st2.json()["items"]) == 0


def test_authorization_enforcement(client, db_session):
    # company cannot call job-seeker list
    comp, ctoken = create_company_and_token(db_session)
    bad = client.get("/api/job-seekers/me/applications", headers=auth_header(ctoken))
    assert bad.status_code == 403

    # job seeker cannot call company list
    s, stoken = create_job_seeker_and_token(db_session, email="x@example.com", verified=True)
    bad2 = client.get("/api/companies/me/applications", headers=auth_header(stoken))
    assert bad2.status_code == 403
