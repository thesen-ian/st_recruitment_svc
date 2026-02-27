from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health() -> Any:
    """Simple health endpoint mounted under /api/health."""
    return {"status": "ok"}


# The following test helper endpoints are intentionally lightweight and safe.
# They live under /api/test/* and are useful for exercising global handlers in tests.

class ValidateModel(BaseModel):
    required_field: int


@router.post("/test/validate")
async def test_validate(payload: ValidateModel) -> Any:
    # Echo back to confirm successful validation when provided
    return {"received": payload.model_dump()}


@router.get("/test/unauthorized")
async def test_unauthorized() -> Any:
    raise HTTPException(status_code=401, detail="Not authenticated")


@router.get("/test/forbidden")
async def test_forbidden() -> Any:
    raise HTTPException(status_code=403, detail="Access denied")
