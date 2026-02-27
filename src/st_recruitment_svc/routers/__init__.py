from __future__ import annotations

import logging
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update, and_
from pydantic import BaseModel

from st_recruitment_svc.auth import get_current_user
import st_recruitment_svc.config as config
from st_recruitment_svc.models.base import (
    User,
    Token,
    FileObject,
    TokenType,
    Visibility,
    get_db,
)
from st_recruitment_svc.storage import open_file, StorageNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter()


class ValidatePayload(BaseModel):
    required_field: int


@router.get("/health")
async def health() -> Any:
    """Simple health endpoint mounted under /api/health."""
    return {"status": "ok"}


# The following test helper endpoints are intentionally lightweight and safe.
# They live under /api/test/* and are useful for exercising global handlers in tests.


@router.post("/test/validate")
async def test_validate(payload: ValidatePayload) -> Any:
    # Return the parsed payload; presence of required_field is enforced by Pydantic
    try:
        # model_dump for pydantic v2 compatibility, fallback to dict()
        data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    except Exception:
        logger.exception("Failed to serialize validated payload")
        data = {"required_field": getattr(payload, "required_field", None)}
    return {"received": data}


@router.get("/test/unauthorized")
async def test_unauthorized() -> Any:
    raise HTTPException(status_code=401, detail="Not authenticated")


@router.get("/test/forbidden")
async def test_forbidden() -> Any:
    raise HTTPException(status_code=403, detail="Access denied")


# --- Private file download token issuance and streaming ---


def _ensure_timezone_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime is timezone-aware in UTC. Treat naive datetimes as UTC.

    This guards against SQLite returning naive datetimes while the service uses aware UTC.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # assume stored in UTC if naive
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@router.post("/files/private/{file_id}/download-token")
def issue_download_token(
    file_id: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_db),
) -> Any:
    """Issue a single-use download token for a private file owned by the requester.

    Returns raw token and expiry. Only stores hash in DB.
    """
    try:
        stmt = select(FileObject).where(FileObject.id == file_id)
        fo = db.execute(stmt).scalars().first()
    except Exception:
        logger.error("DB error loading file object %s", file_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")

    if fo is None:
        raise HTTPException(status_code=404, detail="file not found")

    # Ensure it's private
    vis = fo.visibility
    # visibility might be stored as enum value or string; normalize
    if not (vis == Visibility.private or vis == Visibility.private.value):
        # chosen behavior: return 400 when visibility is not private
        raise HTTPException(status_code=400, detail="file visibility is not private")

    # Ensure owner
    if fo.owner_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="not the owner of the file")

    # Generate raw token suitable for URLs and short enough
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    expires_at = datetime.now(tz=timezone.utc) + timedelta(minutes=15)

    # Persist token row
    try:
        token = Token(
            user_id=current_user.id,
            token_hash=token_hash,
            type=TokenType.download,
            expires_at=expires_at,
            used_at=None,
            file_object_id=fo.id,
        )
        db.add(token)
        db.commit()
        db.refresh(token)
    except Exception:
        logger.error("Failed to create download token", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="failed to create token")

    return {"token": raw_token, "expires_at": expires_at.isoformat()}


@router.get("/files/private/download/{token}")
def download_private_file(
    token: str,
    current_user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    """Validate single-use token and stream associated private file to the owner.

    Enforces: existence, ownership, expiry, single-use. Marks token used only on successful streaming.
    """
    # Hash presented token
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    try:
        stmt = select(Token).where(Token.token_hash == token_hash, Token.type == TokenType.download)
        token_row = db.execute(stmt).scalars().first()
    except Exception:
        logger.error("DB error loading token", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")

    if token_row is None:
        # chosen behavior: 404 when token not found
        raise HTTPException(status_code=404, detail="token not found")

    # Ownership check
    if token_row.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="token does not belong to the authenticated user")

    now = datetime.now(tz=timezone.utc)

    # Expiry check - normalize timezone awareness
    expires_at = _ensure_timezone_aware(token_row.expires_at)
    if expires_at is None or now >= expires_at:
        raise HTTPException(status_code=410, detail="token expired")

    # Unused check
    if token_row.used_at is not None:
        raise HTTPException(status_code=410, detail="token already used")

    # Load associated file object
    try:
        stmt = select(FileObject).where(FileObject.id == token_row.file_object_id)
        fo = db.execute(stmt).scalars().first()
    except Exception:
        logger.error("DB error loading file object for token %s", token_row.id, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")

    if fo is None:
        raise HTTPException(status_code=404, detail="file not found")

    # Ensure file is still private
    vis = fo.visibility
    if not (vis == Visibility.private or vis == Visibility.private.value):
        raise HTTPException(status_code=400, detail="file visibility is not private")

    # Try opening file first to ensure it's streamable
    try:
        fp = open_file(config.PRIVATE_FILES_DIR, fo.storage_path)
    except StorageNotFoundError:
        raise HTTPException(status_code=404, detail="file not found on disk")

    # Attempt atomic mark-used. Use SQL UPDATE where used_at IS NULL to avoid races.
    try:
        # Use now timestamp for used_at
        used_ts = datetime.now(tz=timezone.utc)
        upd = (
            update(Token)
            .where(
                and_(
                    Token.id == token_row.id,
                    Token.used_at.is_(None),
                )
            )
            .values(used_at=used_ts)
        )
        res = db.execute(upd)
        db.commit()
        if res.rowcount != 1:
            # Another request likely consumed it
            try:
                fp.close()
            except Exception:
                pass
            raise HTTPException(status_code=410, detail="token already used")
    except HTTPException:
        raise
    except Exception:
        logger.error("Failed to mark token used %s", token_row.id, exc_info=True)
        try:
            fp.close()
        except Exception:
            pass
        db.rollback()
        raise HTTPException(status_code=500, detail="failed to consume token")

    # Prepare streaming response
    filename = fo.original_filename or f"file-{fo.id}"
    # Content disposition
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(fp, media_type=fo.content_type or "application/octet-stream", headers=headers)

# Include job_seekers router to aggregate routes
try:
    from . import job_seekers
    router.include_router(job_seekers.router)
except Exception:
    logger.debug("Job seekers router not available during import")

# Include companies router
# Import errors should not be silently swallowed to avoid missing routes during tests.
from . import companies
router.include_router(companies.router)
