from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from http.client import responses as HTTP_STATUS_MESSAGES
from starlette.staticfiles import StaticFiles

from st_recruitment_svc.routers import router
from st_recruitment_svc import config
from st_recruitment_svc import scheduler
from st_recruitment_svc import scheduler_jobs

# Configure logger for module
logger = logging.getLogger(__name__)


def _error_envelope(error_type: str, message: str, details: Optional[List[Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "error": {
            "type": error_type,
            "message": message,
        }
    }
    if details is not None:
        payload["error"]["details"] = details
    return payload


# Validate PUBLIC_FILES_DIR before creating/mounting the app
_public_files_dir = getattr(config, "PUBLIC_FILES_DIR", None)
if not _public_files_dir or (isinstance(_public_files_dir, str) and _public_files_dir.strip() == ""):
    # Fail fast if configuration is missing or empty
    raise RuntimeError("PUBLIC_FILES_DIR is not configured")

# Normalize to Path
_public_files_path = Path(_public_files_dir)
try:
    # If configured but doesn't exist, create it deterministically
    if not _public_files_path.exists():
        _public_files_path.mkdir(parents=True, exist_ok=True)
except Exception as e:
    logger.error("Failed to ensure PUBLIC_FILES_DIR exists: %s", _public_files_dir, exc_info=True)
    raise


# Use FastAPI lifespan context manager for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Attempt to start scheduler but do not prevent app startup on failure
    try:
        scheduler.start_scheduler()
        try:
            # After scheduler is started, schedule any registered jobs into it.
            # Use the scheduler instance directly; keep this call minimal to satisfy startup wiring.
            scheduler_jobs.schedule_registered_jobs(scheduler._scheduler)
        except Exception:
            logger.exception("Failed to schedule registered jobs into scheduler")
    except Exception as e:
        logger.error("Scheduler failed to start during app startup", exc_info=True)
    try:
        yield
    finally:
        try:
            scheduler.shutdown_scheduler()
        except Exception as e:
            logger.error("Scheduler failed to shutdown cleanly", exc_info=True)


app = FastAPI(debug=True, lifespan=lifespan)

# Mount all API routes under a single router at /api
app.include_router(router, prefix="/api")

# Mount public static files. Use str() to accept Path or str.
try:
    app.mount("/public", StaticFiles(directory=str(_public_files_path)), name="public")
except Exception:
    logger.error("Failed to mount /public static files from %s", _public_files_path, exc_info=True)
    raise


# Exception handler for request validation errors from FastAPI
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    try:
        details = exc.errors()
    except Exception:  # defensive; ensure we never leak internal state
        logger.error("Failed to extract validation errors", exc_info=True)
        details = []

    payload = _error_envelope("validation_error", "Validation failed", details)
    logger.debug("Validation error response prepared: %s", payload)
    return JSONResponse(status_code=422, content=payload)


# Exception handler for HTTPException to normalize 401 and 403
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    # Choose type based on status code
    if exc.status_code == 401:
        err_type = "unauthorized"
    elif exc.status_code == 403:
        err_type = "forbidden"
    else:
        err_type = "http_error"

    # Provide a safe default message if none provided
    detail = exc.detail if exc.detail else HTTP_STATUS_MESSAGES.get(exc.status_code, "HTTP Error")

    payload = _error_envelope(err_type, str(detail))
    logger.info("HTTP exception handled: status=%s type=%s path=%s", exc.status_code, err_type, request.url.path)
    return JSONResponse(status_code=exc.status_code, content=payload)
