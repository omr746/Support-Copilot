"""Health and readiness probe routes (/healthz, /readyz)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from copilot.api.container import Container, get_container
from copilot.api.schemas import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])


@router.get(
    "/healthz",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Liveness probe",
    description="Confirms that the FastAPI process is alive and responsive.",
)
@router.get("/healthz/", include_in_schema=False)
async def healthz() -> HealthResponse:
    """Liveness probe: returns 200 OK unconditionally when the application is running."""
    return HealthResponse(status="ok")


@router.get(
    "/readyz",
    response_model=ReadyResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadyResponse,
            "description": "One or more critical dependencies are down or not ready.",
        }
    },
    summary="Readiness probe",
    description="Confirms that all required dependencies are ready to serve incoming traffic.",
)
@router.get("/readyz/", include_in_schema=False)
async def readyz(
    container: Annotated[Container, Depends(get_container)],
) -> JSONResponse:
    """Readiness probe: checks registered dependencies; returns 200 if up, 503 if any dependency is down."""
    is_ready, details = await container.check_readiness()
    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    payload = {
        "status": "ready" if is_ready else "unavailable",
        "checks": details,
    }
    return JSONResponse(status_code=status_code, content=payload)
