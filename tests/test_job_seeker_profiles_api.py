from __future__ import annotations

import pytest
from fastapi import HTTPException

from st_recruitment_svc.models.base import User, UserRole, UserStatus
from st_recruitment_svc.auth import create_access_token
from st_recruitment_svc.routers.job_seekers import require_job_seeker_profile


PROFILE_PATH = "/api/job-seekers/me"


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_get_and_put(client):
    r = client.get(PROFILE_PATH)
    assert r.status_code == 401

    r = client.put(PROFILE_PATH, json={})
    assert r.status_code == 401


def test_non_job_seeker_forbidden(db_session, client):
    user = User(
        email="comp@example.com",
        password_hash="x",
        role=UserRole.company,
        status=UserStatus.active,
    )
    db_session.add(user)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role.value})
    headers = _auth_header(token)

    r = client.get(PROFILE_PATH, headers=headers)
    assert r.status_code == 403

    r = client.put(PROFILE_PATH, json={"full_name": "X", "email": "x@e.com"}, headers=headers)
    assert r.status_code == 403


def test_get_returns_404_when_missing(db_session, client):
    user = User(
        email="js@example.com",
        password_hash="x",
        role=UserRole.job_seeker,
        status=UserStatus.active,
    )
    db_session.add(user)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role.value})
    headers = _auth_header(token)

    r = client.get(PROFILE_PATH, headers=headers)
    assert r.status_code == 404

    # gating helper should raise PROFILE_REQUIRED (409)
    with pytest.raises(HTTPException) as exc:
        require_job_seeker_profile(current_user=user, db=db_session)
    assert exc.value.status_code == 409


def test_put_creates_and_get_returns_profile(db_session, client):
    user = User(
        email="js2@example.com",
        password_hash="x",
        role=UserRole.job_seeker,
        status=UserStatus.active,
    )
    db_session.add(user)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role.value})
    headers = _auth_header(token)

    payload = {
        "full_name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "555-1234",
        "location": "Testville",
        "summary": "Experienced dev",
        "skills": ["python", "sql"],
        "experiences": [{"company": "A", "years": 2}],
    }

    r = client.put(PROFILE_PATH, json=payload, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body.get("id")
    assert body.get("user_id") == user.id
    assert body.get("full_name") == payload["full_name"]
    assert body.get("skills") == payload["skills"]

    # Now GET should return the same
    r2 = client.get(PROFILE_PATH, headers=headers)
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2.get("id") == body.get("id")
    assert b2.get("skills") == payload["skills"]

    # gating helper should now return profile object (no exception)
    prof = require_job_seeker_profile(current_user=user, db=db_session)
    assert prof.user_id == user.id


def test_put_updates_profile(db_session, client):
    user = User(
        email="js3@example.com",
        password_hash="x",
        role=UserRole.job_seeker,
        status=UserStatus.active,
    )
    db_session.add(user)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role.value})
    headers = _auth_header(token)

    payload = {"full_name": "Initial", "email": "i@example.com", "skills": ["a"]}
    r = client.put(PROFILE_PATH, json=payload, headers=headers)
    assert r.status_code == 200
    initial = r.json()

    # update
    update_payload = {"full_name": "Updated", "email": "u@example.com", "skills": ["b", "c"]}
    r2 = client.put(PROFILE_PATH, json=update_payload, headers=headers)
    assert r2.status_code == 200
    updated = r2.json()
    assert updated.get("id") == initial.get("id")
    assert updated.get("full_name") == "Updated"
    assert updated.get("skills") == ["b", "c"]
