from __future__ import annotations

from datetime import timedelta
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from st_recruitment_svc.auth import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    get_current_user,
    require_roles,
)
from st_recruitment_svc.models.base import User, UserRole, UserStatus


def test_password_hash_and_verify():
    pw = "s3cretP@ss"
    hashed = hash_password(pw)
    assert hashed != pw
    assert verify_password(pw, hashed)
    assert not verify_password("wrong", hashed)


def test_jwt_create_and_decode_success():
    payload = {"sub": "user-123", "role": UserRole.job_seeker.value}
    token = create_access_token(payload)
    decoded = decode_access_token(token)
    assert decoded.get("sub") == "user-123"
    assert decoded.get("role") == UserRole.job_seeker.value


def test_jwt_invalid_signature():
    payload = {"sub": "u1", "role": UserRole.job_seeker.value}
    token = create_access_token(payload)
    # Tamper token by corrupting the signature segment to ensure invalid signature
    parts = token.split('.')
    assert len(parts) == 3
    sig = parts[2]
    # flip first character of signature (or prepend if too short)
    if len(sig) >= 1:
        tampered_sig = ("A" if sig[0] != "A" else "B") + sig[1:]
    else:
        tampered_sig = "A"
    tampered = parts[0] + "." + parts[1] + "." + tampered_sig
    with pytest.raises(HTTPException) as exc:
        decode_access_token(tampered)
    assert exc.value.status_code == 401


def test_jwt_expired_token():
    payload = {"sub": "u2", "role": UserRole.job_seeker.value}
    token = create_access_token(payload, expires_delta=timedelta(seconds=-10))
    with pytest.raises(HTTPException) as exc:
        decode_access_token(token)
    assert exc.value.status_code == 401


def test_get_current_user_and_role_enforcement(db_session):
    # Create three users: active, suspended, admin
    active = User(
        email="active@example.com",
        password_hash=hash_password("pass"),
        role=UserRole.job_seeker,
        status=UserStatus.active,
    )
    suspended = User(
        email="suspended@example.com",
        password_hash=hash_password("pass"),
        role=UserRole.job_seeker,
        status=UserStatus.suspended,
    )
    admin = User(
        email="admin@example.com",
        password_hash=hash_password("pass"),
        role=UserRole.admin,
        status=UserStatus.active,
    )

    db_session.add_all([active, suspended, admin])
    db_session.commit()

    # Create tokens for users
    token_active = create_access_token({"sub": active.id, "role": active.role.value})
    token_s = create_access_token({"sub": suspended.id, "role": suspended.role.value})
    token_admin = create_access_token({"sub": admin.id, "role": admin.role.value})

    # Build HTTPAuthorizationCredentials objects to pass directly to dependency
    creds_active = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_active)
    creds_s = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_s)
    creds_admin = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_admin)

    # Active user should be allowed
    user = get_current_user(credentials=creds_active, db=db_session)
    assert user.id == active.id

    # Suspended user should get 403
    with pytest.raises(HTTPException) as exc:
        get_current_user(credentials=creds_s, db=db_session)
    assert exc.value.status_code == 403

    # Non-admin accessing admin-only check should be forbidden via require_roles
    admin_check = require_roles({UserRole.admin})
    with pytest.raises(HTTPException) as exc:
        # call dependency directly with current_user from active
        admin_check(current_user=user)
    assert exc.value.status_code == 403

    # Admin accessing admin route should succeed
    admin_user = get_current_user(credentials=creds_admin, db=db_session)
    result = admin_check(current_user=admin_user)
    assert result.id == admin.id
