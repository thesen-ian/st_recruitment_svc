from __future__ import annotations

import pytest
from st_recruitment_svc.models.base import User, UserRole, UserStatus, create_user
from st_recruitment_svc.auth import create_access_token

PATH = "/api/companies/me"


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_get_and_put(client):
    r = client.get(PATH)
    assert r.status_code == 401

    r = client.put(PATH, json={})
    assert r.status_code == 401


def test_non_company_forbidden(db_session, client):
    user = User(
        email="u@example.com",
        password_hash="x",
        role=UserRole.job_seeker,
        status=UserStatus.active,
    )
    db_session.add(user)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role.value})
    headers = _auth_header(token)

    r = client.get(PATH, headers=headers)
    assert r.status_code == 403

    r = client.put(PATH, json={"description": "x"}, headers=headers)
    assert r.status_code == 403


def test_get_returns_profile_for_company(db_session, client):
    # Use helper to auto-create company and profile
    user = create_user(db_session, email="c@example.com", password_hash="x", role=UserRole.company)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role.value})
    headers = _auth_header(token)

    r = client.get(PATH, headers=headers)
    assert r.status_code == 200
    body = r.json()
    # Basic presence of keys
    assert body.get("company_id") == user.id
    assert "description" in body
    assert "website_url" in body


def test_put_updates_profile_and_get_reflects(db_session, client):
    user = create_user(db_session, email="c2@example.com", password_hash="x", role=UserRole.company)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role.value})
    headers = _auth_header(token)

    payload = {
        "description": "We make things",
        "website_url": "https://example.com",
        "industry": "software",
        "size": "50-200",
        "hq_location": "Test City",
    }

    r = client.put(PATH, json=payload, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body.get("description") == payload["description"]
    assert body.get("website_url") == payload["website_url"]
    assert body.get("industry") == payload["industry"]

    # GET should return same
    r2 = client.get(PATH, headers=headers)
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2.get("description") == payload["description"]
    assert b2.get("website_url") == payload["website_url"]


def test_put_does_not_change_business_registration_number(db_session, client):
    # create company with a business_registration_number set via create_user kwargs
    user = create_user(db_session, email="c3@example.com", password_hash="x", role=UserRole.company, business_registration_number="BRN-123")
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role.value})
    headers = _auth_header(token)

    # Attempt to change business_registration_number via payload (should be ignored)
    r = client.put(PATH, json={"business_registration_number": "BRN-999", "description": "desc"}, headers=headers)
    # Request should succeed but BRN unchanged
    assert r.status_code == 200
    body = r.json()
    # business_registration_number originates from user record and must remain unchanged
    assert body.get("business_registration_number") == "BRN-123"
