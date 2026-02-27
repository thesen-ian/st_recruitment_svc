from __future__ import annotations

import pytest
from st_recruitment_svc.models.base import User, UserRole, create_user
from st_recruitment_svc.auth import create_access_token

PATH = "/api/companies/me/logo"


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_post(client):
    r = client.post(PATH)
    assert r.status_code == 401


def test_company_authenticated_post(db_session, client):
    # create a company user and ensure token contains sub and role
    user = create_user(db_session, email="c-logo@example.com", password_hash="x", role=UserRole.company)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role.value})
    headers = _auth_header(token)

    r = client.post(PATH, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body.get("company_id") == user.id
