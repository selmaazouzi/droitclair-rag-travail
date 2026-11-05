"""GET /health — liveness and dependency checks."""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

CheckStatus = Literal["ok", "error"]


class HealthResponse(BaseModel):
    status: CheckStatus
    database: CheckStatus
    # The embedding model check (ARCHITECTURE §11) is added with the dense retrieval (phase 3).


@router.get("/health")
async def health(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HealthResponse:
    try:
        await session.execute(text("SELECT 1"))
        database: CheckStatus = "ok"
    except Exception:
        logger.exception("Health check: database unreachable")
        database = "error"

    overall: CheckStatus = "ok" if database == "ok" else "error"
    if overall != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status=overall, database=database)
