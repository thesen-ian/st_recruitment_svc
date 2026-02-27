from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from st_recruitment_svc.models.base import (
    User,
    UserRole,
    create_user,
    JobPosting,
    JobPostingQuestion,
    JobPostingQuestionOption,
    QuestionType,
    JobPostingState,
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


def create_non_company_and_token(db_session, role=UserRole.job_seeker, email="js@example.com"):
    u = User(email=email, password_hash=hash_password("pass"), role=role)
    db_session.add(u)
    db_session.flush()
    token = create_access_token({"sub": u.id, "role": u.role.value})
    return u, token


# Public job detail tests
def test_public_get_active_job_returns_with_questions_and_options(client, db_session):
    comp, _ = create_company_and_token(db_session)
    db_session.commit()

    # create job with questions/options
    job = JobPosting(company_id=comp.id, state=JobPostingState.active, title="Eng", description="Desc")
    db_session.add(job)
    db_session.flush()

    q1 = JobPostingQuestion(job_posting_id=job.id, type=QuestionType.mcq, prompt="Pick one", is_required=True, position=0)
    db_session.add(q1)
    db_session.flush()
    o1 = JobPostingQuestionOption(question_id=q1.id, label="A", position=0)
    o2 = JobPostingQuestionOption(question_id=q1.id, label="B", position=1)
    db_session.add_all([o1, o2])

    # second question
    q2 = JobPostingQuestion(job_posting_id=job.id, type=QuestionType.text, prompt="Tell us", is_required=False, position=1)
    db_session.add(q2)

    db_session.commit()

    resp = client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == job.id
    assert data["state"] == "active"
    assert len(data["questions"]) == 2
    # ensure ordering by position
    assert data["questions"][0]["prompt"] == "Pick one"
    assert data["questions"][0]["options"][0]["label"] == "A"
    assert data["questions"][0]["options"][1]["label"] == "B"


def test_public_get_draft_or_closed_returns_404(client, db_session):
    comp, _ = create_company_and_token(db_session, email="c2@example.com")
    db_session.commit()

    draft_job = JobPosting(company_id=comp.id, state=JobPostingState.draft, title="D", description="d")
    closed_job = JobPosting(company_id=comp.id, state=JobPostingState.closed, title="C", description="c")
    db_session.add_all([draft_job, closed_job])
    db_session.commit()

    r1 = client.get(f"/api/jobs/{draft_job.id}")
    assert r1.status_code == 404
    r2 = client.get(f"/api/jobs/{closed_job.id}")
    assert r2.status_code == 404


def test_public_get_nonexistent_returns_404(client):
    r = client.get(f"/api/jobs/{str(uuid.uuid4())}")
    assert r.status_code == 404


# Company listing tests
def test_companies_me_jobs_returns_all_states_and_respects_company_isolation(client, db_session):
    # company A
    comp_a, token_a = create_company_and_token(db_session, email="a@example.com")
    # company B
    comp_b, token_b = create_company_and_token(db_session, email="b@example.com")
    db_session.commit()

    # A jobs: draft, active, closed
    j1 = JobPosting(company_id=comp_a.id, state=JobPostingState.draft, title="DraftA", description="x")
    j2 = JobPosting(company_id=comp_a.id, state=JobPostingState.active, title="ActiveA", description="x")
    j3 = JobPosting(company_id=comp_a.id, state=JobPostingState.closed, title="ClosedA", description="x")

    # add questions to one job
    db_session.add_all([j1, j2, j3])
    db_session.flush()

    q = JobPostingQuestion(job_posting_id=j2.id, type=QuestionType.text, prompt="Q", is_required=False, position=0)
    db_session.add(q)
    db_session.commit()

    # B job
    jb = JobPosting(company_id=comp_b.id, state=JobPostingState.active, title="ActiveB", description="y")
    db_session.add(jb)
    db_session.commit()

    # call listing as company A
    resp = client.get("/api/companies/me/jobs", headers=auth_header(token_a))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # should see three jobs for company A
    ids = {j["id"] for j in data}
    assert j1.id in ids and j2.id in ids and j3.id in ids
    # should not include B job
    assert jb.id not in ids
    # ensure questions are embedded for j2
    job_map = {j["id"]: j for j in data}
    assert job_map[j2.id]["questions"][0]["prompt"] == "Q"


def test_companies_me_jobs_auth_required_and_company_role_enforced(client, db_session):
    # unauthenticated
    r = client.get("/api/companies/me/jobs")
    assert r.status_code == 401

    # authenticated but wrong role
    non_company, token = create_non_company_and_token(db_session, role=UserRole.job_seeker, email="js2@example.com")
    db_session.commit()
    r2 = client.get("/api/companies/me/jobs", headers=auth_header(token))
    assert r2.status_code == 403

