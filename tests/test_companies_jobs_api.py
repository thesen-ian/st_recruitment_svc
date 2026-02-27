from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from st_recruitment_svc.models.base import User, UserRole, JobPosting, JobPostingQuestion, JobPostingQuestionOption, QuestionType, JobPostingState
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


def create_admin_and_token(db_session):
    admin = User(email="admin@example.com", password_hash=hash_password("pass"), role=UserRole.admin)
    db_session.add(admin)
    db_session.flush()
    token = create_access_token({"sub": admin.id, "role": admin.role.value})
    return admin, token


def test_create_job_with_mcq_and_options_persists(client, db_session):
    company, token = create_company_and_token(db_session)

    payload = {
        "title": "Engineer",
        "description": "Great role",
        "questions": [
            {
                "type": "mcq",
                "prompt": "Choose",
                "options": [
                    {"label": "First"},
                    {"label": "Second"}
                ]
            }
        ]
    }

    resp = client.post("/api/jobs", json=payload, headers=auth_header(token))
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["state"] == "draft"
    assert len(data["questions"]) == 1
    q = data["questions"][0]
    assert q["type"] == "mcq"
    assert len(q["options"]) == 2
    assert q["options"][0]["label"] == "First"
    assert q["options"][1]["label"] == "Second"

    # Verify persisted in DB
    stmt = select(JobPosting).where(JobPosting.id == data["id"])
    persisted = db_session.execute(stmt).scalars().first()
    assert persisted is not None
    assert persisted.company_id == company.id


def test_create_mcq_with_empty_options_returns_422(client, db_session):
    company, token = create_company_and_token(db_session)
    payload = {
        "title": "Role",
        "description": "Desc",
        "questions": [
            {"type": "mcq", "prompt": "Pick", "options": []}
        ]
    }
    resp = client.post("/api/jobs", json=payload, headers=auth_header(token))
    assert resp.status_code == 422


def test_create_text_with_options_returns_422(client, db_session):
    company, token = create_company_and_token(db_session)
    payload = {
        "title": "Role",
        "description": "Desc",
        "questions": [
            {"type": "text", "prompt": "Tell us", "options": [{"label": "X"}]}
        ]
    }
    resp = client.post("/api/jobs", json=payload, headers=auth_header(token))
    assert resp.status_code == 422


def test_put_updates_and_replaces_questions_owner_and_admin(client, db_session):
    company, token = create_company_and_token(db_session)
    admin, admin_token = create_admin_and_token(db_session)

    # create initial job
    payload = {"title": "T1", "description": "D1", "questions": [{"type": "text", "prompt": "P1"}]}
    r = client.post("/api/jobs", json=payload, headers=auth_header(token))
    assert r.status_code == 201
    job = r.json()

    # owner updates and replaces questions
    update_payload = {"title": "T2", "description": "D2", "questions": [{"type": "mcq", "prompt": "Pick", "options": [{"label": "A"}]}]}
    r2 = client.put(f"/api/jobs/{job['id']}", json=update_payload, headers=auth_header(token))
    assert r2.status_code == 200
    updated = r2.json()
    assert updated["title"] == "T2"
    assert len(updated["questions"]) == 1
    assert updated["questions"][0]["type"] == "mcq"

    # admin can update any job
    admin_update = {"title": "T3", "description": "D3", "questions": []}
    r3 = client.put(f"/api/jobs/{job['id']}", json=admin_update, headers=auth_header(admin_token))
    assert r3.status_code == 200
    assert r3.json()["title"] == "T3"


def test_put_non_owner_company_gets_403(client, db_session):
    company1, token1 = create_company_and_token(db_session, email="c1@example.com")
    company2, token2 = create_company_and_token(db_session, email="c2@example.com")

    payload = {"title": "T1", "description": "D1"}
    r = client.post("/api/jobs", json=payload, headers=auth_header(token1))
    assert r.status_code == 201
    job = r.json()

    r2 = client.put(f"/api/jobs/{job['id']}", json={"title": "X", "description": "Y"}, headers=auth_header(token2))
    assert r2.status_code == 403


def test_cannot_update_closed_job(client, db_session):
    company, token = create_company_and_token(db_session)

    # create job and mark as closed directly in DB
    payload = {"title": "Role", "description": "Desc"}
    r = client.post("/api/jobs", json=payload, headers=auth_header(token))
    job = r.json()

    # set closed state
    stmt = select(JobPosting).where(JobPosting.id == job['id'])
    persisted = db_session.execute(stmt).scalars().first()
    persisted.state = JobPostingState.closed
    db_session.commit()

    r2 = client.put(f"/api/jobs/{job['id']}", json={"title": "New", "description": "New"}, headers=auth_header(token))
    assert r2.status_code == 422


def test_delete_job_owner_and_cascade(client, db_session):
    company, token = create_company_and_token(db_session)
    payload = {
        "title": "Eng",
        "description": "D",
        "questions": [
            {"type": "mcq", "prompt": "Q", "options": [{"label": "A"}]}
        ]
    }
    r = client.post("/api/jobs", json=payload, headers=auth_header(token))
    assert r.status_code == 201
    job = r.json()

    # delete
    r2 = client.delete(f"/api/jobs/{job['id']}", headers=auth_header(token))
    assert r2.status_code == 200

    # verify gone in DB
    stmt = select(JobPosting).where(JobPosting.id == job['id'])
    assert db_session.execute(stmt).scalars().first() is None


