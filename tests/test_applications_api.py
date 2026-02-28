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


@pytest.mark.parametrize("mcq_label", ["mcq"])  # simple marker to allow DB fixtures
def test_happy_path_applies_and_persists(client, db_session, mcq_label):
    # Setup company and job with text + mcq required questions
    company, ctoken = create_company_and_token(db_session)
    payload = {
        "title": "Engineer",
        "description": "Great role",
        "questions": [
            {"type": "text", "prompt": "Tell us about yourself", "is_required": True},
            {"type": "mcq", "prompt": "Choose one", "is_required": True, "options": [{"label": "A"}, {"label": "B"}]},
        ],
    }
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    assert r.status_code == 201, r.text
    job = r.json()

    # publish
    pub = client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))
    assert pub.status_code == 200
    job_active = pub.json()

    # create job seeker and resume
    seeker, stoken = create_job_seeker_and_token(db_session, email="s1@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility="private", purpose="resume", content_type="application/pdf", size_bytes=123, storage_path="/tmp/x")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    # get job with question ids and option ids
    resp = client.get(f"/api/jobs/{job['id']}")
    assert resp.status_code == 200
    job_detail = resp.json()
    questions = job_detail["questions"]
    # map
    text_q = next(q for q in questions if q["type"] == "text")
    mcq_q = next(q for q in questions if q["type"] == "mcq")
    mcq_option_id = mcq_q["options"][0]["id"]

    apply_payload = {
        "selected_resume_id": resume.id,
        "cover_letter": "Please consider me",
        "answers": [
            {"question_id": text_q["id"], "answer_text": "I am great"},
            {"question_id": mcq_q["id"], "selected_option_id": mcq_option_id},
        ],
    }

    ap = client.post(f"/api/jobs/{job['id']}/apply", json=apply_payload, headers=auth_header(stoken))
    assert ap.status_code == 201, ap.text
    data = ap.json()
    assert "application_id" in data

    # verify persisted
    stmt = select(Application).where(Application.id == data["application_id"])
    persisted = db_session.execute(stmt).scalars().first()
    assert persisted is not None
    assert persisted.job_seeker_user_id == seeker.id
    # answers
    stmt2 = select(ApplicationAnswer).where(ApplicationAnswer.application_id == persisted.id)
    answers = db_session.execute(stmt2).scalars().all()
    assert len(answers) == 2


def test_duplicate_apply_returns_409(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "T", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    pub = client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))
    assert pub.status_code == 200

    seeker, stoken = create_job_seeker_and_token(db_session, email="s2@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility="private", purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/x2")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    payload_apply = {"selected_resume_id": resume.id, "answers": []}
    r1 = client.post(f"/api/jobs/{job['id']}/apply", json=payload_apply, headers=auth_header(stoken))
    assert r1.status_code == 201
    r2 = client.post(f"/api/jobs/{job['id']}/apply", json=payload_apply, headers=auth_header(stoken))
    assert r2.status_code == 409

    # only one application exists
    stmt = select(Application).where(Application.job_id == job['id'])
    apps = db_session.execute(stmt).scalars().all()
    assert len(apps) == 1


def test_missing_required_question_rejected(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "T", "description": "D", "questions": [{"type": "text", "prompt": "P", "is_required": True}]}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    pub = client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))
    assert pub.status_code == 200

    seeker, stoken = create_job_seeker_and_token(db_session, email="s3@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility="private", purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/x3")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    payload_apply = {"selected_resume_id": resume.id, "answers": []}
    r = client.post(f"/api/jobs/{job['id']}/apply", json=payload_apply, headers=auth_header(stoken))
    assert r.status_code == 400

    stmt = select(Application).where(Application.job_id == job['id'])
    apps = db_session.execute(stmt).scalars().all()
    assert len(apps) == 0


def test_foreign_question_rejected(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    # job A with one text question
    payload_a = {"title": "A", "description": "A", "questions": [{"type": "text", "prompt": "P", "is_required": False}]}
    r = client.post("/api/jobs", json=payload_a, headers=auth_header(ctoken))
    job_a = r.json()
    client.post(f"/api/jobs/{job_a['id']}/publish", headers=auth_header(ctoken))

    # job B with another question
    payload_b = {"title": "B", "description": "B", "questions": [{"type": "text", "prompt": "Q", "is_required": False}]}
    r2 = client.post("/api/jobs", json=payload_b, headers=auth_header(ctoken))
    job_b = r2.json()
    client.post(f"/api/jobs/{job_b['id']}/publish", headers=auth_header(ctoken))

    # get question id from job_b
    jb = client.get(f"/api/jobs/{job_b['id']}").json()
    foreign_qid = jb['questions'][0]['id']

    seeker, stoken = create_job_seeker_and_token(db_session, email="s4@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility="private", purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/x4")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    payload_apply = {"selected_resume_id": resume.id, "answers": [{"question_id": foreign_qid, "answer_text": "X"}]}
    r = client.post(f"/api/jobs/{job_a['id']}/apply", json=payload_apply, headers=auth_header(stoken))
    assert r.status_code == 400


def test_invalid_option_rejected(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "T", "description": "D", "questions": [{"type": "mcq", "prompt": "P", "is_required": True, "options": [{"label": "A"}]}]}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="s5@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility="private", purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/x5")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    # fetch job detail for question ids
    job_detail = client.get(f"/api/jobs/{job['id']}").json()
    qid = job_detail['questions'][0]['id']

    # Use a random invalid option id
    payload_apply = {"selected_resume_id": resume.id, "answers": [{"question_id": qid, "selected_option_id": "00000000-0000-0000-0000-000000000000"}]}
    r = client.post(f"/api/jobs/{job['id']}/apply", json=payload_apply, headers=auth_header(stoken))
    assert r.status_code == 400


# ---- New tests for FILE answers ----

def test_file_answer_happy_path(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "Upload role", "description": "D", "questions": [{"type": "file", "prompt": "Upload CV", "is_required": True}]}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="file-seeker@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="application_answer", content_type="application/pdf", size_bytes=1024, storage_path="/tmp/file1")
    db_session.add(fo)
    db_session.flush()
    resume_fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/resume1")
    db_session.add(resume_fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=resume_fo.id)
    db_session.add(resume)
    db_session.commit()

    job_detail = client.get(f"/api/jobs/{job['id']}").json()
    qid = job_detail['questions'][0]['id']

    payload_apply = {"selected_resume_id": resume.id, "answers": [{"question_id": qid, "file_object_id": fo.id}]}
    resp = client.post(f"/api/jobs/{job['id']}/apply", json=payload_apply, headers=auth_header(stoken))
    assert resp.status_code == 201, resp.text
    data = resp.json()
    # persisted answer should reference file_object_id
    stmt = select(Application).where(Application.id == data['application_id'])
    app_row = db_session.execute(stmt).scalars().first()
    assert app_row is not None
    ans_stmt = select(ApplicationAnswer).where(ApplicationAnswer.application_id == app_row.id)
    answers = db_session.execute(ans_stmt).scalars().all()
    assert len(answers) == 1
    assert answers[0].file_object_id == fo.id


def test_file_too_large_rejected(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "Upload role", "description": "D", "questions": [{"type": "file", "prompt": "Upload CV", "is_required": True}]}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="bigfile@example.com", verified=True)
    # size > 5MB
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="application_answer", content_type="application/pdf", size_bytes=6 * 1024 * 1024, storage_path="/tmp/large")
    db_session.add(fo)
    db_session.flush()
    resume_fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/res2")
    db_session.add(resume_fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=resume_fo.id)
    db_session.add(resume)
    db_session.commit()

    job_detail = client.get(f"/api/jobs/{job['id']}").json()
    qid = job_detail['questions'][0]['id']

    payload_apply = {"selected_resume_id": resume.id, "answers": [{"question_id": qid, "file_object_id": fo.id}]}
    resp = client.post(f"/api/jobs/{job['id']}/apply", json=payload_apply, headers=auth_header(stoken))
    assert resp.status_code == 413

    # ensure no application persisted
    stmt = select(Application).where(Application.job_id == job['id'])
    apps = db_session.execute(stmt).scalars().all()
    assert len(apps) == 0


def test_file_not_found_returns_404(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "Upload role", "description": "D", "questions": [{"type": "file", "prompt": "Upload CV", "is_required": True}]}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="nofile@example.com", verified=True)
    resume_fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/res3")
    db_session.add(resume_fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=resume_fo.id)
    db_session.add(resume)
    db_session.commit()

    job_detail = client.get(f"/api/jobs/{job['id']}").json()
    qid = job_detail['questions'][0]['id']

    payload_apply = {"selected_resume_id": resume.id, "answers": [{"question_id": qid, "file_object_id": "00000000-0000-0000-0000-000000000000"}]}
    resp = client.post(f"/api/jobs/{job['id']}/apply", json=payload_apply, headers=auth_header(stoken))
    assert resp.status_code == 404

    stmt = select(Application).where(Application.job_id == job['id'])
    apps = db_session.execute(stmt).scalars().all()
    assert len(apps) == 0


def test_file_not_owned_returns_403(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "Upload role", "description": "D", "questions": [{"type": "file", "prompt": "Upload CV", "is_required": True}]}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    owner, _ = create_job_seeker_and_token(db_session, email="owner@example.com", verified=True)
    other, other_token = create_job_seeker_and_token(db_session, email="other@example.com", verified=True)

    fo = FileObject(owner_user_id=owner.id, visibility=Visibility.private.value, purpose="application_answer", content_type="application/pdf", size_bytes=1024, storage_path="/tmp/ownerfile")
    db_session.add(fo)
    db_session.flush()
    resume_fo = FileObject(owner_user_id=other.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/res4")
    db_session.add(resume_fo)
    db_session.flush()
    resume = Resume(user_id=other.id, label="CV", file_object_id=resume_fo.id)
    db_session.add(resume)
    db_session.commit()

    job_detail = client.get(f"/api/jobs/{job['id']}").json()
    qid = job_detail['questions'][0]['id']

    payload_apply = {"selected_resume_id": resume.id, "answers": [{"question_id": qid, "file_object_id": fo.id}]}
    resp = client.post(f"/api/jobs/{job['id']}/apply", json=payload_apply, headers=auth_header(other_token))
    assert resp.status_code == 403

    stmt = select(Application).where(Application.job_id == job['id'])
    apps = db_session.execute(stmt).scalars().all()
    assert len(apps) == 0


def test_file_public_rejected(client, db_session):
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "Upload role", "description": "D", "questions": [{"type": "file", "prompt": "Upload CV", "is_required": True}]}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    seeker, stoken = create_job_seeker_and_token(db_session, email="publicfile@example.com", verified=True)
    fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.public.value, purpose="application_answer", content_type="application/pdf", size_bytes=1024, storage_path="/tmp/pub")
    db_session.add(fo)
    db_session.flush()
    resume_fo = FileObject(owner_user_id=seeker.id, visibility=Visibility.private.value, purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/res5")
    db_session.add(resume_fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=resume_fo.id)
    db_session.add(resume)
    db_session.commit()

    job_detail = client.get(f"/api/jobs/{job['id']}").json()
    qid = job_detail['questions'][0]['id']

    payload_apply = {"selected_resume_id": resume.id, "answers": [{"question_id": qid, "file_object_id": fo.id}]}
    resp = client.post(f"/api/jobs/{job['id']}/apply", json=payload_apply, headers=auth_header(stoken))

    # we require private files and do not attempt to change visibility, so reject
    assert resp.status_code == 400


def test_authorization_checks(client, db_session):
    # company cannot apply
    company, ctoken = create_company_and_token(db_session)
    payload = {"title": "T", "description": "D", "questions": []}
    r = client.post("/api/jobs", json=payload, headers=auth_header(ctoken))
    job = r.json()
    client.post(f"/api/jobs/{job['id']}/publish", headers=auth_header(ctoken))

    # attempt apply as company user
    payload_apply = {"selected_resume_id": "fake", "answers": []}
    r = client.post(f"/api/jobs/{job['id']}/apply", json=payload_apply, headers=auth_header(ctoken))
    assert r.status_code == 403

    # unverified seeker cannot apply
    seeker, stoken = create_job_seeker_and_token(db_session, email="s7@example.com", verified=False)
    fo = FileObject(owner_user_id=seeker.id, visibility="private", purpose="resume", content_type="application/pdf", size_bytes=1, storage_path="/tmp/x7")
    db_session.add(fo)
    db_session.flush()
    resume = Resume(user_id=seeker.id, label="CV", file_object_id=fo.id)
    db_session.add(resume)
    db_session.commit()

    payload_apply2 = {"selected_resume_id": resume.id, "answers": []}
    r2 = client.post(f"/api/jobs/{job['id']}/apply", json=payload_apply2, headers=auth_header(stoken))
    assert r2.status_code == 403
