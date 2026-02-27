from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from http.client import responses as HTTP_STATUS_MESSAGES

from st_recruitment_svc.routers import router

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


app = FastAPI(debug=True)

# Mount all API routes under a single router at /api
app.include_router(router, prefix="/api")


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
