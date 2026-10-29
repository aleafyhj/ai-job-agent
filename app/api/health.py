from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


@router.get("/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    """Process liveness only; does not claim database or worker readiness."""
    return HealthResponse()


@router.get("/ready", tags=["health"], responses={503: {"description": "Database not ready"}})
def ready(request: Request):
    """Database/schema readiness; storage checks join when storage is implemented."""
    engine = request.app.state.db_engine
    if engine is None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "database": "not_configured",
            },
        )
    try:
        with engine.connect() as connection:
            revision = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
        if revision != "0001":
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "database": "migration_required",
                },
            )
    except SQLAlchemyError:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "database": "unavailable",
            },
        )
    return {"status": "ok", "database": "ok"}
