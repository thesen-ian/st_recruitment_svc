from __future__ import annotations

from datetime import datetime, timezone
from st_recruitment_svc.models.base import (
    JobPosting,
    JobPostingState,
)
from sqlalchemy import select
from sqlalchemy.dialects import postgresql


def test_no_q_returns_active_ordered(client, db_session):
    # create three job postings with different created_at, only two active
    now = datetime.now(tz=timezone.utc)
    j_old = JobPosting(company_id="c1", state=JobPostingState.active, title="Old", description="x")
    j_new = JobPosting(company_id="c1", state=JobPostingState.active, title="New", description="x")
    j_draft = JobPosting(company_id="c1", state=JobPostingState.draft, title="Draft", description="x")

    # set deterministic timestamps so ordering is predictable
    j_old.created_at = datetime.fromtimestamp(0, tz=timezone.utc)
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
    # must contain the FTS match operator and tsquery function
    assert "@@" in compiled
    assert "websearch_to_tsquery" in compiled or "plainto_tsquery" in compiled
    # must include a rank function
    assert "ts_rank_cd" in compiled or "ts_rank" in compiled
    # when using postgres FTS branch, the compiled SQL should not use ILIKE/LIKE
    assert "ILIKE" not in compiled.upper()
    assert " LIKE " not in compiled.upper()
    # ordering should include rank desc, created_at desc, id desc in that order
    order_by_section = compiled.upper().split("ORDER BY")[-1]
    assert "RANK" in order_by_section
    assert "CREATED_AT" in order_by_section
    assert "ID" in order_by_section


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


def test_stable_ordering_with_identical_created_at(client, db_session):
    # Seed three active jobs with identical created_at timestamps
    ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
    a = JobPosting(company_id="c7", state=JobPostingState.active, title="T1", description="x")
    b = JobPosting(company_id="c7", state=JobPostingState.active, title="T2", description="x")
    c = JobPosting(company_id="c7", state=JobPostingState.active, title="T3", description="x")
    for obj in (a, b, c):
        obj.created_at = ts
    db_session.add_all([a, b, c])
    db_session.commit()

    # Call the API
    resp = client.get("/api/search/jobs")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]

    # Query the DB directly using the expected ordering to build expected sequence
    stmt = select(JobPosting).where(JobPosting.state == JobPostingState.active).order_by(JobPosting.created_at.desc(), JobPosting.id.desc())
    res = db_session.execute(stmt).scalars().all()
    expected_ids = [r.id for r in res if r.title in {"T1", "T2", "T3"}]
    returned_ids = [it["id"] for it in items if it["title"] in {"T1", "T2", "T3"}]

    assert returned_ids == expected_ids
