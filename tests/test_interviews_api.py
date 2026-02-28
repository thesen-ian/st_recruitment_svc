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
    # create_access_token expects sub claim and role
    payload = {"sub": user.id, "role": user.role.value}
    return create_access_token(payload)


def test_propose_and_accept_and_reschedule_cap(client, db_session):
    # Setup company, job posting, job seeker, fileobject, resume, and application
    company = User(email="c@example.com", password_hash="x", role=UserRole.company, company_name="C")
    job_seeker = User(email="j@example.com", password_hash="y", role=UserRole.job_seeker)
    db_session.add_all([company, job_seeker])
    db_session.flush()

    job = JobPosting(company_id=company.id, title="T", description="D")
    db_session.add(job)
    db_session.flush()

    # create file object and resume for job seeker to satisfy FK if needed
    fo = FileObject(owner_user_id=job_seeker.id, visibility="private", purpose="resume", content_type="application/pdf", size_bytes=100, storage_path="/tmp/x")
    db_session.add(fo)
    db_session.flush()

    resume = Resume(user_id=job_seeker.id, label="R", file_object_id=fo.id)
    db_session.add(resume)
    db_session.flush()

    app = Application(job_id=job.id, job_seeker_user_id=job_seeker.id, selected_resume_id=resume.id)
    db_session.add(app)
    db_session.commit()

    company_token = make_token_for_user(company)
    seeker_token = make_token_for_user(job_seeker)

    # Company proposes interview
    start = (datetime.now(tz=timezone.utc) + timedelta(days=1)).isoformat()
    payload = {
        "start_at": start,
        "duration_minutes": 30,
        "format": "video",
        "location_or_link": "https://meet.example/abc",
        "interviewer_names": ["Alice", " Bob "]
    }
    resp = client.post(f"/api/applications/{app.id}/interview/propose", json=payload, headers={"Authorization": f"Bearer {company_token}"})
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["application_id"] == app.id
    interview_id = data["interview_id"]
    assert data["status"] == "proposed"

    # Job seeker accepts interview
    resp = client.post(f"/api/interviews/{interview_id}/accept", headers={"Authorization": f"Bearer {seeker_token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "accepted"

    # Create a fresh interview to test reschedule cap
    # propose another interview
    start2 = (datetime.now(tz=timezone.utc) + timedelta(days=2)).isoformat()
    payload2 = {
        "start_at": start2,
        "duration_minutes": 45,
        "format": "phone",
        "location_or_link": "+123456",
        "interviewer_names": ["Z"]
    }
    resp = client.post(f"/api/applications/{app.id}/interview/propose", json=payload2, headers={"Authorization": f"Bearer {company_token}"})
    assert resp.status_code == 201, resp.text
    new_int = resp.json()["interview_id"]

    # Make 3 reschedule requests (allowed)
    for i in range(3):
        pref = [(datetime.now(tz=timezone.utc) + timedelta(days=3 + i)).isoformat()]
        rpayload = {"reason": f"need to reschedule {i}", "preferred_times": pref}
        resp = client.post(f"/api/interviews/{new_int}/reschedule-request", json=rpayload, headers={"Authorization": f"Bearer {seeker_token}"})
        assert resp.status_code == 200, resp.text

    # Fourth request should be rejected due to cap
    pref = [(datetime.now(tz=timezone.utc) + timedelta(days=7)).isoformat()]
    rpayload = {"reason": "one more", "preferred_times": pref}
    resp = client.post(f"/api/interviews/{new_int}/reschedule-request", json=rpayload, headers={"Authorization": f"Bearer {seeker_token}"})
    assert resp.status_code == 400
