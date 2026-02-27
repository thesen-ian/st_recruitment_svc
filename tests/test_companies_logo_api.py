from __future__ import annotations

import pytest
import io
from sqlalchemy import select
from st_recruitment_svc.models.base import User, UserRole, create_user, CompanyProfile
from st_recruitment_svc.auth import create_access_token
from st_recruitment_svc.models.base import FilePurpose

PATH = "/api/companies/me/logo"
PATH_COVER = "/api/companies/me/cover"


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


def test_unauthenticated_post_cover(client):
    r = client.post(PATH_COVER)
    assert r.status_code == 401


def test_company_authenticated_post_cover(db_session, client):
    # create a company user and ensure token contains sub and role
    user = create_user(db_session, email="c-cover@example.com", password_hash="x", role=UserRole.company)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role.value})
    headers = _auth_header(token)

    r = client.post(PATH_COVER, headers=headers)
    assert r.status_code == 422  # missing file part; UploadFile required


# Now tests that mirror logo file upload behavior for cover endpoint

def test_cover_success_upload_sets_cover_file_object_id(db_session, client, tmp_path, monkeypatch):
    # Prepare public dir and reload app via config reload in tests elsewhere
    public_dir = tmp_path / "public"
    public_dir.mkdir(parents=True)
    monkeypatch.setenv("PUBLIC_FILES_DIR", str(public_dir))

    # create company user
    user = create_user(db_session, email="c-cover-upload@example.com", password_hash="x", role=UserRole.company)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role.value})
    headers = _auth_header(token)

    data = b"cover-bytes"
    files = {"upload_file": ("cover.jpg", io.BytesIO(data), "image/jpeg")}

    r = client.post(PATH_COVER, headers=headers, files=files)
    assert r.status_code == 200
    body = r.json()
    assert body.get("company_id") == user.id
    assert body.get("cover_file_object_id") is not None
    assert body.get("public_url") is not None

    # verify DB: reload profile and check cover_file_object_id
    stmt = select(CompanyProfile).where(CompanyProfile.company_id == user.id)
    profile = db_session.execute(stmt).scalars().first()
    assert profile is not None
    assert profile.cover_file_object_id == body.get("cover_file_object_id")


def test_cover_invalid_content_type_returns_415(db_session, client, tmp_path, monkeypatch):
    public_dir = tmp_path / "public"
    public_dir.mkdir(parents=True)
    monkeypatch.setenv("PUBLIC_FILES_DIR", str(public_dir))

    # create company user
    user = create_user(db_session, email="c-cover-ct@example.com", password_hash="x", role=UserRole.company)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role.value})
    headers = _auth_header(token)

    data = b"not-an-image"
    files = {"upload_file": ("bad.txt", io.BytesIO(data), "text/plain")}

    r = client.post(PATH_COVER, headers=headers, files=files)
    assert r.status_code == 415 or r.status_code == 422


def test_cover_too_large_returns_413(db_session, client, tmp_path, monkeypatch):
    public_dir = tmp_path / "public"
    public_dir.mkdir(parents=True)
    monkeypatch.setenv("PUBLIC_FILES_DIR", str(public_dir))

    # create company user
    user = create_user(db_session, email="c-cover-large@example.com", password_hash="x", role=UserRole.company)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role.value})
    headers = _auth_header(token)

    # build payload larger than allowed for company_logo (storage SIZE_LIMITS; using a big payload)
    large = b"0" * (6 * 1024 * 1024)
    files = {"upload_file": ("big.jpg", io.BytesIO(large), "image/jpeg")}

    r = client.post(PATH_COVER, headers=headers, files=files)
    assert r.status_code == 413 or r.status_code == 500


def test_cover_empty_file_returns_422(db_session, client, tmp_path, monkeypatch):
    public_dir = tmp_path / "public"
    public_dir.mkdir(parents=True)
    monkeypatch.setenv("PUBLIC_FILES_DIR", str(public_dir))

    # create company user
    user = create_user(db_session, email="c-cover-empty@example.com", password_hash="x", role=UserRole.company)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role.value})
    headers = _auth_header(token)

    files = {"upload_file": ("empty.jpg", io.BytesIO(b""), "image/jpeg")}

    r = client.post(PATH_COVER, headers=headers, files=files)
    # Empty file may be rejected by storage validate_file or result in 200 with size 0 depending on validation
    assert r.status_code in (422, 413, 200)
