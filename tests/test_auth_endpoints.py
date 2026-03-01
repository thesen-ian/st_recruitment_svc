import hashlib
from datetime import timedelta

import pytest

from st_recruitment_svc.routers import auth as auth_module
from st_recruitment_svc.models.auth import (
    User,
    EmailVerificationToken,
    PasswordResetToken,
)
from st_recruitment_svc.auth.passwords import hash_password
from st_recruitment_svc.auth.rate_limit import _now_utc


def test_register_verify_login_happy_path(client, db_session):
    # override token generator to return deterministic token and capture it
    token_container = {}

    def override_token():
        token_container["token"] = "fixed-test-token"
        return token_container["token"]

    client.app.dependency_overrides[auth_module.token_generator] = override_token

    # Register
    resp = client.post(
        "/api/auth/register",
        json={"email": "User@Example.com", "password": "secret123", "role": "job_seeker"},
    )
    assert resp.status_code == 201

    raw_token = token_container.get("token")
    assert raw_token is not None

    # Verify
    resp = client.post("/api/auth/verify-email", json={"token": raw_token})
    assert resp.status_code == 200

    # Login
    resp = client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": "secret123"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("access_token")
    assert data.get("token_type") == "bearer"

    client.app.dependency_overrides.pop(auth_module.token_generator, None)


def test_verification_token_expiry_and_reuse(client, db_session):
    # create user and expired token
    user = User(email="a@example.com", password_hash=hash_password("pw"), role="job_seeker", status="active")
    db_session.add(user)
    db_session.flush()
    expired = EmailVerificationToken(
        user_id=user.id,
        token_hash=hashlib.sha256(b"oldtoken").hexdigest(),
        expires_at=_now_utc() - timedelta(hours=1),
    )
    db_session.add(expired)
    db_session.commit()

    # Attempt verify with expired token
    resp = client.post("/api/auth/verify-email", json={"token": "oldtoken"})
    assert resp.status_code == 400
    assert resp.json()["detail"].lower().find("expired") != -1

    # create usable token
    token_raw = "usable-token"
    usable = EmailVerificationToken(
        user_id=user.id,
        token_hash=hashlib.sha256(token_raw.encode()).hexdigest(),
        expires_at=_now_utc() + timedelta(hours=1),
    )
    db_session.add(usable)
    db_session.commit()

    # First use succeeds
    resp = client.post("/api/auth/verify-email", json={"token": token_raw})
    assert resp.status_code == 200

    # Second use fails due to used_at
    resp = client.post("/api/auth/verify-email", json={"token": token_raw})
    assert resp.status_code == 400
    assert "used" in resp.json()["detail"].lower()


def test_password_reset_token_reuse(client, db_session):
    # create user and password reset token
    user = User(email="b@example.com", password_hash=hash_password("pw2"), role="job_seeker", status="active")
    db_session.add(user)
    db_session.flush()

    token_raw = "reset-token"
    pr = PasswordResetToken(
        user_id=user.id,
        token_hash=hashlib.sha256(token_raw.encode()).hexdigest(),
        expires_at=_now_utc() + timedelta(hours=1),
    )
    db_session.add(pr)
    db_session.commit()

    # Confirm reset
    resp = client.post(
        "/api/auth/password-reset/confirm", json={"token": token_raw, "new_password": "newpass123"}
    )
    assert resp.status_code == 200

    # Second confirm fails
    resp = client.post(
        "/api/auth/password-reset/confirm", json={"token": token_raw, "new_password": "newpass123"}
    )
    assert resp.status_code == 400
    assert "used" in resp.json()["detail"].lower()


def test_login_rate_limit_blocks_after_five_attempts(client, db_session):
    # create user
    pwd = "correctpw"
    user = User(email="c@example.com", password_hash=hash_password(pwd), role="job_seeker", status="active")
    user.email_verified_at = _now_utc()
    db_session.add(user)
    db_session.commit()

    # perform 5 failed attempts
    for i in range(5):
        resp = client.post(
            "/api/auth/login", json={"email": "c@example.com", "password": "wrongpw"}
        )
        assert resp.status_code == 401

    # 6th attempt should be blocked with 429
    resp = client.post("/api/auth/login", json={"email": "c@example.com", "password": "wrongpw"})
    assert resp.status_code == 429


def test_suspended_user_login_rejected(client, db_session):
    user = User(email="d@example.com", password_hash=hash_password("pw"), role="job_seeker", status="suspended")
    user.email_verified_at = _now_utc()
    db_session.add(user)
    db_session.commit()

    resp = client.post("/api/auth/login", json={"email": "d@example.com", "password": "pw"})
    assert resp.status_code == 403
    assert "suspend" in resp.json()["detail"].lower()


def test_password_reset_request_does_not_leak_existence(client, db_session):
    # request for non-existing email
    resp = client.post("/api/auth/password-reset/request", json={"email": "nope@example.com"})
    assert resp.status_code == 200
    msg_non = resp.json().get("detail")

    # create existing user and request
    user = User(email="e@example.com", password_hash=hash_password("pw"), role="job_seeker", status="active")
    db_session.add(user)
    db_session.commit()

    resp = client.post("/api/auth/password-reset/request", json={"email": "e@example.com"})
    assert resp.status_code == 200
    msg_yes = resp.json().get("detail")

    assert msg_non == msg_yes
