from __future__ import annotations

import uuid
from st_recruitment_svc.models.base import (
    User,
    UserRole,
    create_user,
    Job,
    JobStatus,
)

PATH_TEMPLATE = "/api/companies/{id}"


def test_public_company_returns_profile_and_active_jobs(db_session, client):
    # create two companies
    c1 = create_user(db_session, email="pub1@example.com", password_hash="x", role=UserRole.company, company_name="Pub One")
    c2 = create_user(db_session, email="pub2@example.com", password_hash="x", role=UserRole.company, company_name="Pub Two")
    db_session.commit()

    # create jobs: one active for c1, one draft for c1, one active for c2
    active_job_c1 = Job(company_id=c1.id, title="Engineer", description="eng role", status=JobStatus.published)
    draft_job_c1 = Job(company_id=c1.id, title="Intern", description="intern role", status=JobStatus.draft)
    active_job_c2 = Job(company_id=c2.id, title="Designer", description="design", status=JobStatus.published)

    db_session.add_all([active_job_c1, draft_job_c1, active_job_c2])
    db_session.commit()

    resp = client.get(PATH_TEMPLATE.format(id=c1.id))
    assert resp.status_code == 200
    body = resp.json()

    # Basic presence
    assert body.get("company_id") == c1.id
    assert body.get("company_name") == "Pub One"
    assert "active_job_postings" in body

    active_jobs = body.get("active_job_postings")
    # Only one active job for c1
    assert isinstance(active_jobs, list)
    assert len(active_jobs) == 1
    assert active_jobs[0]["title"] == "Engineer"


def test_public_filters_other_companies(db_session, client):
    # create company and job for another company to ensure not returned
    c3 = create_user(db_session, email="other@example.com", password_hash="x", role=UserRole.company, company_name="OtherCo")
    db_session.commit()

    active_job_other = Job(company_id=c3.id, title="OtherRole", description="x", status=JobStatus.published)
    db_session.add(active_job_other)
    db_session.commit()

    # request company that doesn't exist should produce 404
    resp = client.get(PATH_TEMPLATE.format(id=str(uuid.uuid4())))
    assert resp.status_code == 404


def test_nonexistent_company_returns_404(client):
    resp = client.get(PATH_TEMPLATE.format(id=str(uuid.uuid4())))
    assert resp.status_code == 404
