import io
import os
import tempfile
import uuid

import pytest

from st_recruitment_svc.storage import (
    build_relative_path,
    write_file,
    open_file,
    validate_file,
    FileValidationError,
    StorageError,
    StorageNotFoundError,
    SIZE_LIMITS,
)
from st_recruitment_svc.models.base import FilePurpose, Visibility, User


def test_build_relative_path_sharding_and_extension():
    owner = "user-123"
    file_id = "11112222-3333-4444-5555-666677778888"
    rel = build_relative_path(Visibility.public, FilePurpose.company_logo, owner, file_id, original_filename="logo.PNG")
    # Expect purpose/owner/first2/fileid.ext
    assert rel.startswith(f"{FilePurpose.company_logo.value}/{owner}/")
    parts = rel.split("/")
    assert parts[2] == file_id[:2]
    assert parts[3].startswith(file_id)
    assert parts[3].lower().endswith(".png")


def test_path_traversal_protection(tmp_path):
    root = str(tmp_path)
    data = b"hello"
    with pytest.raises(StorageError):
        write_file(root, "../evil.txt", io.BytesIO(data))
    with pytest.raises(StorageError):
        write_file(root, "/absolute.txt", io.BytesIO(data))


def test_validation_rejects_oversize_and_bad_content_type():
    # oversize for resume
    max_resume = SIZE_LIMITS[FilePurpose.resume.value]
    with pytest.raises(FileValidationError):
        validate_file(FilePurpose.resume, max_resume + 1, "application/pdf")

    # wrong content type for logo
    with pytest.raises(FileValidationError):
        validate_file(FilePurpose.company_logo, 1024, "application/pdf")


def test_atomic_write_and_no_temp_left(tmp_path):
    root = str(tmp_path)
    rel = "resume/user1/aa/testfile.pdf"
    data = b"x" * 1024
    final_abs, size = write_file(root, rel, io.BytesIO(data))
    assert os.path.exists(final_abs)
    assert size == len(data)
    # ensure no temp files remain in the directories
    for root_dir, dirs, files in os.walk(root):
        for fname in files:
            assert not fname.endswith('.tmp') and not fname.startswith('.')


def test_open_file_not_found(tmp_path):
    root = str(tmp_path)
    with pytest.raises(StorageNotFoundError):
        open_file(root, "does/not/exist.txt")


def test_create_file_object_db_integration(db_session, tmp_path):
    # Create a user to satisfy FK
    user = User(id=str(uuid.uuid4()), email="u@example.com", password_hash="x", role="job_seeker")
    db_session.add(user)
    db_session.commit()

    # write a small file
    rel = f"resume/{user.id}/aa/{str(uuid.uuid4())}.pdf"
    data = b"pdfbytes"
    root = str(tmp_path)
    final_abs, size = write_file(root, rel, io.BytesIO(data))
    # Use storage.create_file_object but import lazily to avoid circulars
    from st_recruitment_svc.storage import create_file_object

    fo = create_file_object(
        db_session,
        owner_user_id=user.id,
        visibility=Visibility.private,
        purpose=FilePurpose.resume,
        content_type="application/pdf",
        size_bytes=size,
        storage_path=rel,
        original_filename="cv.pdf",
    )
    assert fo.id is not None
    assert fo.owner_user_id == user.id
    assert fo.storage_path == rel
