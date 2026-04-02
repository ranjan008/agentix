from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    version: str = "4.0.0"


@router.get("/healthz", response_model=HealthResponse)
async def healthz():
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=HealthResponse)
async def readyz():
    return HealthResponse(status="ready")
