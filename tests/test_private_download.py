import hashlib
import os
from datetime import datetime, timedelta, timezone
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from st_recruitment_svc.config import PRIVATE_FILES_DIR as CONFIG_PRIVATE_FILES_DIR
from st_recruitment_svc.storage import build_relative_path, write_file
from st_recruitment_svc.models.base import User, Token, FileObject, TokenType, Visibility, FilePurpose
from st_recruitment_svc.auth import create_access_token
from st_recruitment_svc import config


def _sha256_hex(val: str) -> str:
    return hashlib.sha256(val.encode("utf-8")).hexdigest()


def test_issue_and_download_token_happy_path(client: TestClient, db_session, tmp_path, monkeypatch):
    # configure private dir
    private_dir = tmp_path / "private"
    private_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "PRIVATE_FILES_DIR", str(private_dir))

    # create user
    user = User(id=secrets.token_hex(8), email="u@example.com", password_hash="x", role="job_seeker")
    db_session.add(user)
    db_session.commit()

    # create a file on disk
    rel = build_relative_path(Visibility.private, FilePurpose.resume, user.id, "fileid123", original_filename="cv.pdf")
    data = b"pdf-data"
    abs_path, size = write_file(str(private_dir), rel, data)

    # create file_object row
    fo = FileObject(
        owner_user_id=user.id,
        visibility=Visibility.private.value,
        purpose=FilePurpose.resume.value,
        original_filename="cv.pdf",
        content_type="application/pdf",
        size_bytes=size,
        storage_path=rel,
    )
    db_session.add(fo)
    db_session.commit()
    db_session.refresh(fo)

    # Issue token
    access = create_access_token({"sub": user.id, "role": user.role})
    headers = {"Authorization": f"Bearer {access}"}
    resp = client.post(f"/api/files/private/{fo.id}/download-token", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body and "expires_at" in body
    raw_token = body["token"]
    assert isinstance(raw_token, str) and len(raw_token) > 0

    # DB token exists and hashed
    stmt = select(Token).where(Token.file_object_id == fo.id)
    db_token = db_session.execute(stmt).scalars().first()
    assert db_token is not None
    assert db_token.token_hash == _sha256_hex(raw_token)
    assert db_token.used_at is None

    # Download with token
    resp2 = client.get(f"/api/files/private/download/{raw_token}", headers=headers)
    assert resp2.status_code == 200
    assert resp2.content == data

    # token used
    db_session.refresh(db_token)
    assert db_token.used_at is not None

    # second attempt fails single-use
    resp3 = client.get(f"/api/files/private/download/{raw_token}", headers=headers)
    assert resp3.status_code == 410


def test_expired_token(client: TestClient, db_session, tmp_path, monkeypatch):
    private_dir = tmp_path / "private"
    private_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "PRIVATE_FILES_DIR", str(private_dir))

    user = User(id=secrets.token_hex(8), email="a@example.com", password_hash="x", role="job_seeker")
    db_session.add(user)
    db_session.commit()

    rel = build_relative_path(Visibility.private, FilePurpose.resume, user.id, "fileid-exp", original_filename="cv.pdf")
    data = b"pdf"
    write_file(str(private_dir), rel, data)

    fo = FileObject(
        owner_user_id=user.id,
        visibility=Visibility.private.value,
        purpose=FilePurpose.resume.value,
        original_filename=None,
        content_type="application/pdf",
        size_bytes=len(data),
        storage_path=rel,
    )
    db_session.add(fo)
    db_session.commit()
    db_session.refresh(fo)

    raw = "expiredtoken"
    # ensure created_at precedes expires_at to satisfy DB CHECK constraint
    created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    t = Token(
        user_id=user.id,
        token_hash=_sha256_hex(raw),
        type=TokenType.download,
        expires_at=expires_at,
        used_at=None,
        file_object_id=fo.id,
        created_at=created_at,
    )
    db_session.add(t)
    db_session.commit()

    access = create_access_token({"sub": user.id, "role": user.role})
    headers = {"Authorization": f"Bearer {access}"}
    resp = client.get(f"/api/files/private/download/{raw}", headers=headers)
    assert resp.status_code == 410


def test_wrong_user_cannot_use_token(client: TestClient, db_session, tmp_path, monkeypatch):
    private_dir = tmp_path / "private"
    private_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "PRIVATE_FILES_DIR", str(private_dir))

    user_a = User(id=secrets.token_hex(8), email="a@example.com", password_hash="x", role="job_seeker")
    user_b = User(id=secrets.token_hex(8), email="b@example.com", password_hash="x", role="job_seeker")
    db_session.add_all([user_a, user_b])
    db_session.commit()

    rel = build_relative_path(Visibility.private, FilePurpose.resume, user_a.id, "fileid-1")
    data = b"x"
    write_file(str(private_dir), rel, data)

    fo = FileObject(
        owner_user_id=user_a.id,
        visibility=Visibility.private.value,
        purpose=FilePurpose.resume.value,
        original_filename=None,
        content_type="application/pdf",
        size_bytes=len(data),
        storage_path=rel,
    )
    db_session.add(fo)
    db_session.commit()
    db_session.refresh(fo)

    raw = "tok-use"
    t = Token(
        user_id=user_a.id,
        token_hash=_sha256_hex(raw),
        type=TokenType.download,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        used_at=None,
        file_object_id=fo.id,
    )
    db_session.add(t)
    db_session.commit()

    access_b = create_access_token({"sub": user_b.id, "role": user_b.role})
    headers = {"Authorization": f"Bearer {access_b}"}
    resp = client.get(f"/api/files/private/download/{raw}", headers=headers)
    assert resp.status_code == 403


def test_suspended_user_blocked(client: TestClient, db_session, tmp_path, monkeypatch):
    private_dir = tmp_path / "private"
    private_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "PRIVATE_FILES_DIR", str(private_dir))

    user = User(id=secrets.token_hex(8), email="s@example.com", password_hash="x", role="job_seeker")
    # set suspended status directly using string matching DB default
    user.status = "suspended"
    db_session.add(user)
    db_session.commit()

    rel = build_relative_path(Visibility.private, FilePurpose.resume, user.id, "fileid-s")
    data = b"y"
    write_file(str(private_dir), rel, data)

    fo = FileObject(
        owner_user_id=user.id,
        visibility=Visibility.private.value,
        purpose=FilePurpose.resume.value,
        original_filename=None,
        content_type="application/pdf",
        size_bytes=len(data),
        storage_path=rel,
    )
    db_session.add(fo)
    db_session.commit()
    db_session.refresh(fo)

    raw = "suspendtok"
    t = Token(
        user_id=user.id,
        token_hash=_sha256_hex(raw),
        type=TokenType.download,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        used_at=None,
        file_object_id=fo.id,
    )
    db_session.add(t)
    db_session.commit()

    access = create_access_token({"sub": user.id, "role": user.role})
    headers = {"Authorization": f"Bearer {access}"}
    # get_current_user should deny suspended user before we reach endpoint semantics
    resp = client.get(f"/api/files/private/download/{raw}", headers=headers)
    assert resp.status_code == 403


def test_non_owner_cannot_issue_token(client: TestClient, db_session, tmp_path, monkeypatch):
    private_dir = tmp_path / "private"
    private_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "PRIVATE_FILES_DIR", str(private_dir))

    owner = User(id=secrets.token_hex(8), email="own@example.com", password_hash="x", role="job_seeker")
    other = User(id=secrets.token_hex(8), email="other@example.com", password_hash="x", role="job_seeker")
    db_session.add_all([owner, other])
    db_session.commit()

    rel = build_relative_path(Visibility.private, FilePurpose.resume, owner.id, "file-owner")
    data = b"z"
    write_file(str(private_dir), rel, data)

    fo = FileObject(
        owner_user_id=owner.id,
        visibility=Visibility.private.value,
        purpose=FilePurpose.resume.value,
        original_filename=None,
        content_type="application/pdf",
        size_bytes=len(data),
        storage_path=rel,
    )
    db_session.add(fo)
    db_session.commit()
    db_session.refresh(fo)

    access_other = create_access_token({"sub": other.id, "role": other.role})
    headers = {"Authorization": f"Bearer {access_other}"}
    resp = client.post(f"/api/files/private/{fo.id}/download-token", headers=headers)
    assert resp.status_code == 403


def test_visibility_not_private_rejected(client: TestClient, db_session, tmp_path, monkeypatch):
    private_dir = tmp_path / "private"
    private_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "PRIVATE_FILES_DIR", str(private_dir))

    user = User(id=secrets.token_hex(8), email="v@example.com", password_hash="x", role="job_seeker")
    db_session.add(user)
    db_session.commit()

    rel = build_relative_path(Visibility.public, FilePurpose.company_logo, user.id, "file-public")
    data = b"logo"
    write_file(str(private_dir), rel, data)

    fo = FileObject(
        owner_user_id=user.id,
        visibility=Visibility.public.value,
        purpose=FilePurpose.company_logo.value,
        original_filename="logo.txt",
        content_type="image/png",
        size_bytes=len(data),
        storage_path=rel,
    )
    db_session.add(fo)
    db_session.commit()
    db_session.refresh(fo)

    access = create_access_token({"sub": user.id, "role": user.role})
    headers = {"Authorization": f"Bearer {access}"}
    resp = client.post(f"/api/files/private/{fo.id}/download-token", headers=headers)
    assert resp.status_code == 400
