from __future__ import annotations

from datetime import datetime, timedelta, timezone
from st_recruitment_svc.models.base import (
    JobPosting,
    JobPostingState,
)
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql


def test_no_q_returns_active_ordered(client, db_session):
    # create three job postings with different created_at, only two active
    now = datetime.now(tz=timezone.utc)
    j_old = JobPosting(company_id="c1", state=JobPostingState.active, title="Old", description="x")
    j_new = JobPosting(company_id="c1", state=JobPostingState.active, title="New", description="x")
    j_draft = JobPosting(company_id="c1", state=JobPostingState.draft, title="Draft", description="x")
    # adjust created_at to control ordering
    j_old.created_at = now - timedelta(days=2)
    j_new.created_at = now
    db_session.add_all([j_old, j_new, j_draft])
    db_session.commit()

    resp = client.get("/api/search/jobs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body["items"]
    # should only include active jobs in recency order
    assert len(items) == 2
    assert items[0]["title"] == "New"
    assert items[1]["title"] == "Old"


def test_sqlite_fallback_q_search_does_not_crash_and_matches(client, db_session):
    # on sqlite, q should fallback to ilike across title/description/company_name
    j = JobPosting(company_id="c2", state=JobPostingState.active, title="Engineer", description="Build stuff", company_name="EngCo")
    db_session.add(j)
    db_session.commit()

    resp = client.get("/api/search/jobs", params={"q": "engineer"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) >= 1
    titles = {it["title"] for it in body["items"]}
    assert "Engineer" in titles


def test_postgres_fts_sql_generation_contains_operators():
    # compile the actual statement builder used by the router to ensure FTS usage
    from st_recruitment_svc.routers.search import _build_search_statement

    stmt, rank_expr, use_fts = _build_search_statement(q="python developer", page=1, dialect_name="postgresql")
    compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}))
    assert "@@" in compiled
    assert "websearch_to_tsquery" in compiled or "plainto_tsquery" in compiled


def test_filters_anded_location_and_job_type(client, db_session):
    j1 = JobPosting(company_id="c3", state=JobPostingState.active, title="A1", description="x", location="NY", employment_type="full-time")
    j2 = JobPosting(company_id="c3", state=JobPostingState.active, title="A2", description="x", location="NY", employment_type="contract")
    db_session.add_all([j1, j2])
    db_session.commit()

    resp = client.get("/api/search/jobs", params={"location": "NY", "job_type": "full-time"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["title"] == "A1"


def test_pagination_returns_20_then_remaining(client, db_session):
    # create 25 active jobs
    for i in range(25):
        jp = JobPosting(company_id="c4", state=JobPostingState.active, title=f"Job{i}", description="x")
        db_session.add(jp)
    db_session.commit()

    r1 = client.get("/api/search/jobs", params={"page": 1})
    assert r1.status_code == 200
    b1 = r1.json()
    assert len(b1["items"]) == 20

    r2 = client.get("/api/search/jobs", params={"page": 2})
    assert r2.status_code == 200
    b2 = r2.json()
    assert len(b2["items"]) == 5


def test_salary_range_filtering(client, db_session):
    j1 = JobPosting(company_id="c5", state=JobPostingState.active, title="Low", description="x", salary_min=0, salary_max=50000)
    j2 = JobPosting(company_id="c5", state=JobPostingState.active, title="Mid", description="x", salary_min=50001, salary_max=100000)
    j3 = JobPosting(company_id="c5", state=JobPostingState.active, title="High", description="x", salary_min=100001, salary_max=200000)
    db_session.add_all([j1, j2, j3])
    db_session.commit()

    # search for jobs that can pay at least 60000
    resp = client.get("/api/search/jobs", params={"salary_min": 60000})
    assert resp.status_code == 200
    titles = {it["title"] for it in resp.json()["items"]}
    assert "Mid" in titles and "High" in titles
    assert "Low" not in titles


def test_skills_param_ignored_when_no_schema_field(client, db_session):
    # schema lacks skills on JobPosting; passing skills should not error
    j = JobPosting(company_id="c6", state=JobPostingState.active, title="S1", description="x")
    db_session.add(j)
    db_session.commit()

    resp = client.get("/api/search/jobs", params={"skills": "python,sql"})
    assert resp.status_code == 200
    # since no skills field, it behaves like no filter and returns the job
    assert any(it["title"] == "S1" for it in resp.json()["items"]) 
