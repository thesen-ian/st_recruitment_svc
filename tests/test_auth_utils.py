import pytest
from datetime import datetime, timedelta
from fastapi import HTTPException, Depends

from st_recruitment_svc.auth.passwords import hash_password, verify_password
from st_recruitment_svc.auth.jwt import create_access_token, decode_and_verify_token
from st_recruitment_svc.config import JWT_SECRET_KEY, JWT_ALGORITHM

import jwt as pyjwt

from st_recruitment_svc.models.auth import User
from st_recruitment_svc.auth.dependencies import get_current_user, require_roles
from st_recruitment_svc.app import app


def test_password_hash_and_verify():
    plain = "S3cr3t-pass"
    hashed = hash_password(plain)
    assert hashed != plain
    assert verify_password(plain, hashed) is True
    assert verify_password("wrong", hashed) is False


def test_jwt_create_and_decode():
    token = create_access_token(subject_user_id=123, role="admin")
    payload = decode_and_verify_token(token)
    assert payload["sub"] == "123"
    assert payload["role"] == "admin"


def test_jwt_expired_is_rejected():
    past = datetime.utcnow() - timedelta(minutes=10)
    payload = {"sub": "1", "role": "admin", "iat": int(past.timestamp()), "exp": int(past.timestamp())}
    token = pyjwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    with pytest.raises(HTTPException):
        decode_and_verify_token(token)


def test_jwt_tampered_is_rejected():
    token = create_access_token(subject_user_id=1, role="admin")
    tampered = token + "x"
    with pytest.raises(HTTPException):
        decode_and_verify_token(tampered)


def _add_route(path: str):
    # add a test-only route to exercise dependency
    @app.get(path)
    def _route(user = Depends(get_current_user)):
        return {"id": user.id, "email": user.email}


def _add_role_route(path: str, roles: set):
    @app.get(path)
    def _route(user = Depends(require_roles(roles))):
        return {"ok": True}


def test_get_current_user_dependency(client, db_session):
    # create user
    u = User(email="a@example.com", password_hash=hash_password("pwd"), role="job_seeker", status="active")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)

    _add_route("/test-current-user-success")
    token = create_access_token(subject_user_id=u.id, role=u.role)
    resp = client.get("/test-current-user-success", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "a@example.com"


def test_suspended_user_is_forbidden(client, db_session):
    u = User(email="b@example.com", password_hash=hash_password("pwd"), role="job_seeker", status="suspended")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)

    _add_route("/test-current-user-suspended")
    token = create_access_token(subject_user_id=u.id, role=u.role)
    resp = client.get("/test-current-user-suspended", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json().get("detail") == "Account suspended"


def test_missing_or_invalid_auth_header(client):
    _add_route("/test-current-user-noheader")
    resp = client.get("/test-current-user-noheader")
    assert resp.status_code == 401

    # malformed header
    resp2 = client.get("/test-current-user-noheader", headers={"Authorization": "Bad token"})
    assert resp2.status_code == 401


def test_require_roles_enforced(client, db_session):
    # create admin and job_seeker users
    admin = User(email="admin@example.com", password_hash=hash_password("pwd"), role="admin", status="active")
    seeker = User(email="seek@example.com", password_hash=hash_password("pwd"), role="job_seeker", status="active")
    db_session.add_all([admin, seeker])
    db_session.commit()
    db_session.refresh(admin)
    db_session.refresh(seeker)

    _add_role_route("/test-role-admin-only", {"admin"})
    token_admin = create_access_token(subject_user_id=admin.id, role=admin.role)
    token_seeker = create_access_token(subject_user_id=seeker.id, role=seeker.role)

    resp_ok = client.get("/test-role-admin-only", headers={"Authorization": f"Bearer {token_admin}"})
    assert resp_ok.status_code == 200

    resp_forbidden = client.get("/test-role-admin-only", headers={"Authorization": f"Bearer {token_seeker}"})
    assert resp_forbidden.status_code == 403
    assert resp_forbidden.json().get("detail") == "Insufficient permissions"
