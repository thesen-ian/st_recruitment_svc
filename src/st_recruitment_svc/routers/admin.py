from __future__ import annotations

from fastapi import APIRouter, Depends

from st_recruitment_svc.auth import require_admin

# Apply admin-only RBAC at router level so all /admin/* endpoints are protected
router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin())])


@router.get("/ping")
async def admin_ping() -> dict:
    """Minimal admin-only endpoint used for access-control tests.

    Real admin endpoints will live under this router in future subtasks.
    """
    return {"status": "ok"}
