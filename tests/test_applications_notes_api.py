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
    ApplicationNote,
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
def test_company_can_create_note_and_persist(client, db_session, marker):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "Engineer", "description": "Great role", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    assert r.status_code == 201
    job = r.json()
    pub = client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))
    assert pub.status_code == 200

    seeker, stoken = create_job_seeker_and_token(db_session, email="notes-seeker@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=123, storage_path="/tmp/n1")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # company adds a note
    note_payload = {"note_text": "Candidate strong on backend"}
    r2 = client.post(f"/api/applications/{app_id}/notes", json=note_payload, headers=auth_header(ctoken))
    assert r2.status_code == 201
    body = r2.json()
    assert body["application_id"] == app_id
    assert body["note_text"] == "Candidate strong on backend"
    assert body["created_by_user_id"] == company.id
    assert "created_at" in body

    # verify DB row
    stmt = select(ApplicationNote).where(ApplicationNote.application_id == app_id)
    notes = db_session.execute(stmt).scalars().all()
    assert len(notes) == 1
    assert notes[0].note_text == "Candidate strong on backend"
    assert notes[0].created_by_user_id == company.id


def test_job_seeker_cannot_create_note_and_other_company_cannot(client, db_session):
    # setup
    company, ctoken = create_company_and_token(db_session)
    other_company, other_ctoken = create_company_and_token(db_session, email="other@example.com")
    payload = {"title": "Eng", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="notes2-seeker@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/n2")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # job seeker attempt
    r2 = client.post(f"/api/applications/{app_id}/notes", json={"note_text": "n"}, headers=auth_header(stoken))
    assert r2.status_code == 403

    # other company attempt
    r3 = client.post(f"/api/applications/{app_id}/notes", json={"note_text": "n"}, headers=auth_header(other_ctoken))
    assert r3.status_code == 403


def test_empty_note_text_invalid_and_not_exposed_to_job_seeker(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "Eng", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="notes3-seeker@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/n3")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    ap = client.post(f"/api/jobs/{job['id']}/apply", json={"selected_resume_id": resume.id, "answers": []}, headers=auth_header(stoken))
    assert ap.status_code == 201
    app_id = ap.json()["application_id"]

    # empty note_text should be rejected (validation)
    r2 = client.post(f"/api/applications/{app_id}/notes", json={"note_text": "   "}, headers=auth_header(ctoken))
    assert r2.status_code == 422

    # add a valid note
    r3 = client.post(f"/api/applications/{app_id}/notes", json={"note_text": "private"}, headers=auth_header(ctoken))
    assert r3.status_code == 201

    # job seeker detail should not include notes
    got = client.get(f"/api/applications/{app_id}", headers=auth_header(stoken))
    assert got.status_code == 200
    body = got.json()
    # Detailed response must not include any note fields
    assert "notes" not in body
    # no leakage of note_text
    assert "private" not in str(body)

    # job seeker list should not include note fields
    lst = client.get("/api/job-seekers/me/applications", headers=auth_header(stoken))
    assert lst.status_code == 200
    items = lst.json().get("items", [])
    for item in items:
        assert "note_text" not in item
