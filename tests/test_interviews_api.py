from datetime import datetime, timezone, timedelta

from st_recruitment_svc.models.base import (
    User,
    UserRole,
    FileObject,
    Resume,
    JobPosting,
    Application,
)
from st_recruitment_svc.auth import create_access_token


def make_token_for_user(user):
    payload = {"sub": user.id, "role": user.role.value}
    return create_access_token(payload)


def setup_entities(db_session):
    company = User(email="c@example.com", password_hash="x", role=UserRole.company, company_name="C")
    other_company = User(email="c2@example.com", password_hash="x2", role=UserRole.company, company_name="C2")
    job_seeker = User(email="j@example.com", password_hash="y", role=UserRole.job_seeker)
    other_seeker = User(email="j2@example.com", password_hash="y2", role=UserRole.job_seeker)
    db_session.add_all([company, other_company, job_seeker, other_seeker])
    db_session.flush()

    job = JobPosting(company_id=company.id, title="T", description="D")
    db_session.add(job)
    db_session.flush()

    fo = FileObject(owner_user_id=job_seeker.id, visibility="private", purpose="resume", content_type="application/pdf", size_bytes=100, storage_path="/tmp/x")
    db_session.add(fo)
    db_session.flush()

    resume = Resume(user_id=job_seeker.id, label="R", file_object_id=fo.id)
    db_session.add(resume)
    db_session.flush()

    app = Application(job_id=job.id, job_seeker_user_id=job_seeker.id, selected_resume_id=resume.id)
    db_session.add(app)
    db_session.commit()

    return {
        "company": company,
        "other_company": other_company,
        "job_seeker": job_seeker,
        "other_seeker": other_seeker,
        "job": job,
        "application": app,
    }


def propose_interview(client, token, app_id, start_days=1, duration=30, fmt="video", location="https://meet.example/abc", interviewers=None):
    if interviewers is None:
        interviewers = ["Alice"]
    start = (datetime.now(tz=timezone.utc) + timedelta(days=start_days)).isoformat()
    payload = {
        "start_at": start,
        "duration_minutes": duration,
        "format": fmt,
        "location_or_link": location,
        "interviewer_names": interviewers,
    }
    return client.post(f"/api/applications/{app_id}/interview/propose", json=payload, headers={"Authorization": f"Bearer {token}"})


def test_happy_path_propose_accept_reschedule_cancel(client, db_session):
    ent = setup_entities(db_session)
    company_token = make_token_for_user(ent["company"])
    seeker_token = make_token_for_user(ent["job_seeker"])

    # Company proposes interview
    resp = propose_interview(client, company_token, ent["application"].id, interviewers=["Alice", " Bob "])
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["application_id"] == ent["application"].id
    interview_id = data["interview_id"]
    assert data["status"] == "proposed"
    assert data["reschedule_count"] == 0

    # Job seeker accepts interview
    resp = client.post(f"/api/interviews/{interview_id}/accept", headers={"Authorization": f"Bearer {seeker_token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "accepted"

    # Job seeker requests reschedule (first time)
    rpayload = {"reason": "need to reschedule", "preferred_times": [(datetime.now(tz=timezone.utc) + timedelta(days=3)).isoformat()]}
    resp = client.post(f"/api/interviews/{interview_id}/reschedule-request", json=rpayload, headers={"Authorization": f"Bearer {seeker_token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "reschedule_requested"
    assert data["reschedule_count"] == 1

    # Company cancels interview
    resp = client.post(f"/api/interviews/{interview_id}/cancel", headers={"Authorization": f"Bearer {company_token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "cancelled"


def test_unauthenticated_and_wrong_role_and_forbidden(client, db_session):
    ent = setup_entities(db_session)
    company_token = make_token_for_user(ent["company"])
    seeker_token = make_token_for_user(ent["job_seeker"])
    other_company_token = make_token_for_user(ent["other_company"])
    other_seeker_token = make_token_for_user(ent["other_seeker"])

    # Unauthenticated propose
    start = (datetime.now(tz=timezone.utc) + timedelta(days=1)).isoformat()
    payload = {
        "start_at": start,
        "duration_minutes": 30,
        "format": "video",
        "location_or_link": "https://meet.example/abc",
        "interviewer_names": ["Alice"],
    }
    resp = client.post(f"/api/applications/{ent['application'].id}/interview/propose", json=payload)
    assert resp.status_code == 401

    # Wrong role: job seeker proposing
    resp = client.post(f"/api/applications/{ent['application'].id}/interview/propose", json=payload, headers={"Authorization": f"Bearer {seeker_token}"})
    assert resp.status_code == 403

    # Wrong ownership: other company trying to propose
    resp = client.post(f"/api/applications/{ent['application'].id}/interview/propose", json=payload, headers={"Authorization": f"Bearer {other_company_token}"})
    assert resp.status_code == 403

    # Propose properly with company
    resp = client.post(f"/api/applications/{ent['application'].id}/interview/propose", json=payload, headers={"Authorization": f"Bearer {company_token}"})
    assert resp.status_code == 201, resp.text
    interview_id = resp.json()["interview_id"]

    # Unauthenticated accept/reschedule/cancel should be rejected
    resp = client.post(f"/api/interviews/{interview_id}/accept")
    assert resp.status_code == 401
    rpayload = {"reason": "x", "preferred_times": [(datetime.now(tz=timezone.utc) + timedelta(days=3)).isoformat()]}
    resp = client.post(f"/api/interviews/{interview_id}/reschedule-request", json=rpayload)
    assert resp.status_code == 401
    resp = client.post(f"/api/interviews/{interview_id}/cancel")
    assert resp.status_code == 401

    # Wrong role: company accepting
    resp = client.post(f"/api/interviews/{interview_id}/accept", headers={"Authorization": f"Bearer {company_token}"})
    assert resp.status_code == 403

    # Wrong role: company requesting reschedule
    resp = client.post(f"/api/interviews/{interview_id}/reschedule-request", json=rpayload, headers={"Authorization": f"Bearer {company_token}"})
    assert resp.status_code == 403

    # Wrong role: job seeker cancelling
    resp = client.post(f"/api/interviews/{interview_id}/cancel", headers={"Authorization": f"Bearer {seeker_token}"})
    assert resp.status_code == 403

    # Wrong ownership: other company tries to cancel
    resp = client.post(f"/api/interviews/{interview_id}/cancel", headers={"Authorization": f"Bearer {other_company_token}"})
    assert resp.status_code == 403

    # Wrong ownership: other seeker tries to accept
    resp = client.post(f"/api/interviews/{interview_id}/accept", headers={"Authorization": f"Bearer {other_seeker_token}"})
    assert resp.status_code == 403

    # Wrong ownership: other seeker tries to reschedule
    resp = client.post(f"/api/interviews/{interview_id}/reschedule-request", json=rpayload, headers={"Authorization": f"Bearer {other_seeker_token}"})
    assert resp.status_code == 403


def test_not_found_and_state_conflicts(client, db_session):
    ent = setup_entities(db_session)
    company_token = make_token_for_user(ent["company"])
    seeker_token = make_token_for_user(ent["job_seeker"])

    # Non-existent application propose -> 404
    start = (datetime.now(tz=timezone.utc) + timedelta(days=1)).isoformat()
    payload = {
        "start_at": start,
        "duration_minutes": 30,
        "format": "video",
        "location_or_link": "https://meet.example/abc",
        "interviewer_names": ["Alice"],
    }
    resp = client.post(f"/api/applications/non-existent/interview/propose", json=payload, headers={"Authorization": f"Bearer {company_token}"})
    assert resp.status_code == 404

    # Propose interview
    resp = propose_interview(client, company_token, ent["application"].id)
    assert resp.status_code == 201
    interview_id = resp.json()["interview_id"]

    # Accept with non-existent interview -> 404
    resp = client.post(f"/api/interviews/notfound/accept", headers={"Authorization": f"Bearer {seeker_token}"})
    assert resp.status_code == 404

    # Reschedule with non-existent -> 404
    rpayload = {"reason": "x", "preferred_times": [(datetime.now(tz=timezone.utc) + timedelta(days=3)).isoformat()]}
    resp = client.post(f"/api/interviews/notfound/reschedule-request", json=rpayload, headers={"Authorization": f"Bearer {seeker_token}"})
    assert resp.status_code == 404

    # Cancel non-existent -> 404
    resp = client.post(f"/api/interviews/notfound/cancel", headers={"Authorization": f"Bearer {company_token}"})
    assert resp.status_code == 404

    # Cancel the interview then ensure accept/reschedule are rejected with 409
    resp = client.post(f"/api/interviews/{interview_id}/cancel", headers={"Authorization": f"Bearer {company_token}"})
    assert resp.status_code == 200

    # Accept cancelled -> 409
    resp = client.post(f"/api/interviews/{interview_id}/accept", headers={"Authorization": f"Bearer {seeker_token}"})
    assert resp.status_code == 409

    # Reschedule cancelled -> 409
    rpayload = {"reason": "x", "preferred_times": [(datetime.now(tz=timezone.utc) + timedelta(days=3)).isoformat()]}
    resp = client.post(f"/api/interviews/{interview_id}/reschedule-request", json=rpayload, headers={"Authorization": f"Bearer {seeker_token}"})
    assert resp.status_code == 409


def test_reschedule_cap_enforcement(client, db_session):
    ent = setup_entities(db_session)
    company_token = make_token_for_user(ent["company"])
    seeker_token = make_token_for_user(ent["job_seeker"])

    # Propose new interview
    resp = propose_interview(client, company_token, ent["application"].id, start_days=2, interviewers=["Z"]) 
    assert resp.status_code == 201
    interview_id = resp.json()["interview_id"]

    # Make 3 reschedule requests (allowed)
    for i in range(3):
        pref = [(datetime.now(tz=timezone.utc) + timedelta(days=3 + i)).isoformat()]
        rpayload = {"reason": f"need to reschedule {i}", "preferred_times": pref}
        resp = client.post(f"/api/interviews/{interview_id}/reschedule-request", json=rpayload, headers={"Authorization": f"Bearer {seeker_token}"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["reschedule_count"] == i + 1

    # Fourth request should be rejected due to cap
    pref = [(datetime.now(tz=timezone.utc) + timedelta(days=7)).isoformat()]
    rpayload = {"reason": "one more", "preferred_times": pref}
    resp = client.post(f"/api/interviews/{interview_id}/reschedule-request", json=rpayload, headers={"Authorization": f"Bearer {seeker_token}"})
    assert resp.status_code == 400

    # Ensure error message mentions limit/max attempts in any supported shape
    try:
        jb = {}
        try:
            jb = resp.json()
        except Exception:
            jb = {}
        detail = ""
        if isinstance(jb, dict):
            if isinstance(jb.get("detail"), str) and jb.get("detail"):
                detail = jb.get("detail")
            elif isinstance(jb.get("error"), dict):
                detail = jb.get("error").get("message", "")
            else:
                # fallback to common keys
                detail = jb.get("message", "") or jb.get("error", "")
        assert isinstance(detail, str)
        assert ("limit" in detail.lower()) or ("max" in detail.lower()) or ("attempt" in detail.lower())
    except Exception:
        # be explicit in failure for better debugging
        assert False, f"Expected textual detail mentioning limit, got: {resp.text}"
