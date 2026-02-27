from __future__ import annotations

import io
import logging
import os
import shutil
import tempfile
import uuid
from typing import BinaryIO, Optional, Tuple

from sqlalchemy.exc import SQLAlchemyError

from st_recruitment_svc.models.base import FileObject, FilePurpose, Visibility
from st_recruitment_svc.models.base import Base

# Keep logging consistent with project conventions
logger = logging.getLogger(__name__)

# Exceptions
class StorageError(Exception):
    pass


class FileValidationError(StorageError):
    pass


class StorageNotFoundError(StorageError):
    pass


# Size limits (bytes)
_MB = 1024 * 1024
SIZE_LIMITS = {
    FilePurpose.resume.value: 10 * _MB,
    FilePurpose.application_answer.value: 10 * _MB,
    FilePurpose.company_logo.value: 5 * _MB,
    FilePurpose.company_cover.value: 10 * _MB,
}

# Content type allowlists
CONTENT_ALLOWLIST = {
    FilePurpose.resume.value: {"application/pdf"},
    FilePurpose.application_answer.value: {"application/pdf"},
    FilePurpose.company_logo.value: {"image/png", "image/jpeg", "image/webp"},
    FilePurpose.company_cover.value: {"image/png", "image/jpeg", "image/webp"},
}


def _safe_ext_from_filename(filename: Optional[str]) -> str:
    """Extract a safe extension (including leading dot) from filename or return empty string.
    Only allow alphanumeric extensions up to length 10 to avoid surprises.
    """
    if not filename:
        return ""
    name = os.path.basename(filename)
    if "." not in name:
        return ""
    _, ext = os.path.splitext(name)
    if not ext:
        return ""
    # sanitize: ext should be like .pdf .png
    # allow letters, numbers, dot and up to 10 chars
    if len(ext) > 10:
        return ""
    # allow only typical characters
    if all(c.isalnum() or c == '.' for c in ext):
        return ext.lower()
    return ""


def build_relative_path(
    visibility: Visibility | str,
    purpose: FilePurpose | str,
    owner_user_id: str,
    file_id: str,
    original_filename: Optional[str] = None,
    content_type: Optional[str] = None,
) -> str:
    """Build deterministic, collision-resistant relative path.

    Form: {purpose}/{owner_user_id}/{file_id[0:2]}/{file_id}{ext}
    The ext is preserved only if it can be reliably derived from original filename.
    """
    # Normalize inputs
    purpose_val = purpose.value if hasattr(purpose, "value") else str(purpose)
    visibility_val = visibility.value if hasattr(visibility, "value") else str(visibility)

    # file_id expected to be uuid string; keep as-is but remove unsafe chars
    safe_file_id = str(file_id)

    # create extension if possible
    ext = _safe_ext_from_filename(original_filename)

    rel = os.path.join(purpose_val, owner_user_id, safe_file_id[0:2], safe_file_id + ext)
    # Use posix-style path separators for storage_path consistency
    return rel.replace("\\", "/")


def _ensure_directory_for(root_dir: str, relative_path: str) -> str:
    dirpath = os.path.dirname(os.path.join(root_dir, relative_path))
    os.makedirs(dirpath, exist_ok=True)
    return dirpath


def validate_file(purpose: FilePurpose | str, size_bytes: int, content_type: str) -> None:
    """Validate size and content type for a purpose.
    Raises FileValidationError on failure.
    """
    purpose_val = purpose.value if hasattr(purpose, "value") else str(purpose)
    try:
        max_size = SIZE_LIMITS[purpose_val]
    except KeyError:
        # Unknown purpose: be conservative and disallow
        raise FileValidationError(f"Unknown file purpose: {purpose_val}")

    if size_bytes < 0:
        raise FileValidationError("size_bytes must be non-negative")

    if size_bytes > max_size:
        raise FileValidationError(f"file exceeds maximum size of {max_size} bytes for purpose {purpose_val}")

    allowlist = CONTENT_ALLOWLIST.get(purpose_val)
    if allowlist is not None and content_type not in allowlist:
        raise FileValidationError(f"content type '{content_type}' is not allowed for purpose {purpose_val}")


def write_file(root_dir: str, relative_path: str, file_stream: BinaryIO | bytes | bytearray) -> Tuple[str, int]:
    """Atomically write stream to disk under root_dir/relative_path.

    Returns (absolute_path, size_bytes_written).
    Protects against path traversal by ensuring final path is inside root_dir.
    """
    # Prevent absolute paths and normalize
    if os.path.isabs(relative_path):
        raise StorageError("relative_path must be relative")

    # Normalize and ensure no .. segments
    normalized = os.path.normpath(relative_path)
    if normalized.startswith("..") or ".." in normalized.split(os.path.sep):
        raise StorageError("relative_path must not contain path traversal segments")

    abs_root = os.path.abspath(root_dir)
    final_abs = os.path.abspath(os.path.join(abs_root, normalized))
    if not (final_abs == abs_root or final_abs.startswith(abs_root + os.sep)):
        raise StorageError("resolved path is outside of storage root")

    # ensure directory exists
    dirpath = _ensure_directory_for(abs_root, normalized)

    # Write to a temp file in the same directory for atomicity
    temp_name = f".{uuid.uuid4().hex}.tmp"
    temp_path = os.path.join(dirpath, temp_name)
    try:
        # Write in binary chunks
        total = 0
        # If file_stream is bytes-like or BytesIO
        if isinstance(file_stream, (bytes, bytearray)):
            with open(temp_path, "wb") as f:
                f.write(file_stream)
                total = len(file_stream)
        else:
            # assume file-like
            with open(temp_path, "wb") as f:
                while True:
                    chunk = file_stream.read(8192)
                    if not chunk:
                        break
                    if isinstance(chunk, str):
                        chunk = chunk.encode()
                    f.write(chunk)
                    total += len(chunk)
        # Move into final location atomically
        final_dir = os.path.dirname(final_abs)
        os.makedirs(final_dir, exist_ok=True)
        os.replace(temp_path, final_abs)
        return final_abs, total
    except Exception as e:
        logger.error("Failed to write file %s", final_abs, exc_info=True)
        # cleanup temp if exists
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            logger.error("Failed to remove temp file %s", temp_path, exc_info=True)
        raise StorageError("failed to write file to disk") from e


def open_file(root_dir: str, relative_path: str) -> BinaryIO:
    """Open file for reading. Raises StorageNotFoundError if missing.
    Returns a binary file object that the caller must close.
    """
    if os.path.isabs(relative_path):
        raise StorageNotFoundError("relative_path must be relative")
    normalized = os.path.normpath(relative_path)
    if normalized.startswith("..") or ".." in normalized.split(os.path.sep):
        raise StorageNotFoundError("relative_path must not contain path traversal segments")

    abs_root = os.path.abspath(root_dir)
    final_abs = os.path.abspath(os.path.join(abs_root, normalized))
    if not (final_abs == abs_root or final_abs.startswith(abs_root + os.sep)):
        raise StorageNotFoundError("resolved path is outside of storage root")

    if not os.path.exists(final_abs) or not os.path.isfile(final_abs):
        raise StorageNotFoundError("file not found")

    try:
        return open(final_abs, "rb")
    except Exception as e:
        logger.error("Failed to open file %s", final_abs, exc_info=True)
        raise StorageNotFoundError("file cannot be opened") from e


# Guidance for future implementers:
# To create a PUBLIC file that will be served under /public:
# 1) Compute a deterministic relative storage path with build_relative_path(...).
# 2) Persist bytes into the configured public root using write_file(PUBLIC_FILES_DIR, storage_path, data).
#    In tests set PUBLIC_FILES_DIR via monkeypatch.setenv("PUBLIC_FILES_DIR", str(tmp_path))
#    and reload st_recruitment_svc.config and st_recruitment_svc.app so the app mounts /public.
# 3) Create the DB metadata row with create_file_object(db_session, owner_user_id, Visibility.public, ...,
#    storage_path=storage_path, ...). This commits the ORM row and returns FileObject.
# 4) The public URL path is reachable at /public/{storage_path} because the FastAPI app mounts
#    the configured PUBLIC_FILES_DIR at /public in src/st_recruitment_svc/app.py.
# The canonical helpers are build_relative_path, write_file, and create_file_object.


def create_file_object(
    db_session,
    owner_user_id: str,
    visibility: Visibility | str,
    purpose: FilePurpose | str,
    content_type: str,
    size_bytes: int,
    storage_path: str,
    original_filename: Optional[str] = None,
) -> FileObject:
    """Create FileObject ORM row. On DB failure attempt to remove file at storage_path.

    storage_path must be relative (no leading slash).
    Returns the persisted FileObject.
    """
    # ensure we store relative path only
    if os.path.isabs(storage_path):
        raise StorageError("storage_path must be relative")

    fo = FileObject(
        owner_user_id=owner_user_id,
        visibility=visibility.value if hasattr(visibility, "value") else str(visibility),
        purpose=purpose.value if hasattr(purpose, "value") else str(purpose),
        content_type=content_type,
        size_bytes=size_bytes,
        storage_path=storage_path,
        original_filename=original_filename,
    )
    try:
        db_session.add(fo)
        db_session.commit()
        db_session.refresh(fo)
        return fo
    except SQLAlchemyError as e:
        logger.error("DB insert failed for file object %s", storage_path, exc_info=True)
        db_session.rollback()
        # best-effort cleanup: remove file from the appropriate root only
        try:
            from st_recruitment_svc.config import PUBLIC_FILES_DIR, PRIVATE_FILES_DIR

            vis_val = visibility.value if hasattr(visibility, "value") else str(visibility)
            root = PUBLIC_FILES_DIR if vis_val == Visibility.public.value else PRIVATE_FILES_DIR

            # compute abs_path before entering the try to guarantee variable exists
            abs_path = os.path.join(root, storage_path)
            try:
                if os.path.exists(abs_path):
                    os.remove(abs_path)
            except Exception:
                logger.warning("Failed to cleanup orphan file %s", abs_path, exc_info=True)
        except Exception:
            logger.error("Failed during orphan cleanup", exc_info=True)
        raise StorageError("failed to persist file metadata") from e


# INTERNAL helper: canonical UploadFile handling pattern
# Summary: When accepting a Starlette/FastAPI UploadFile in synchronous code, read bytes using
# upload_file.file.read() (sync file-like) rather than awaiting upload_file.read().
# After reading bytes, use validate_file, build_relative_path, write_file to persist into the
# PUBLIC_FILES_DIR, then create_file_object(...) to persist DB metadata. Public URL is /public/{storage_path}.
# This helper centralizes that pattern for tests and future endpoints.
from starlette.datastructures import UploadFile as StarletteUploadFile


def persist_uploadfile_as_public(
    db_session,
    owner_user_id: str,
    upload_file: StarletteUploadFile,
    purpose: FilePurpose,
):
    """Persist a Starlette UploadFile as a public FileObject and return (FileObject, public_url).

    This is a synchronous helper intended for use in router handlers or tests where sync code
    reads upload_file.file (a file-like object). It validates content type and size using
    validate_file and writes bytes into PUBLIC_FILES_DIR via write_file.
    """
    try:
        # read bytes from the underlying file-like object (sync pattern)
        # Note: upload_file.read() is async; use upload_file.file.read() in sync contexts
        upload_file.file.seek(0)
        data = upload_file.file.read()
        if data is None:
            data = b""
        if isinstance(data, str):
            data = data.encode()

        content_type = getattr(upload_file, "content_type", None) or "application/octet-stream"
        size = len(data)

        # validate against purpose limits
        validate_file(purpose, size, content_type)

        # generate file id and storage path
        file_id = uuid.uuid4().hex

        rel = build_relative_path(Visibility.public, purpose, owner_user_id, file_id, original_filename=getattr(upload_file, "filename", None), content_type=content_type)

        # import config lazily so tests can set env and reload config before calling
        from st_recruitment_svc.config import PUBLIC_FILES_DIR

        # write bytes to public root
        abs_path, written = write_file(PUBLIC_FILES_DIR, rel, data)

        # create DB row
        fo = create_file_object(
            db_session,
            owner_user_id=owner_user_id,
            visibility=Visibility.public,
            purpose=purpose,
            content_type=content_type,
            size_bytes=written,
            storage_path=rel,
            original_filename=getattr(upload_file, "filename", None),
        )

        public_url = f"/public/{rel}"
        return fo, public_url
    except Exception as e:
        logger.error("Failed to persist upload file for owner %s: %s", owner_user_id, e, exc_info=True)
        raise
