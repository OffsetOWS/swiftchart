from fastapi import APIRouter, Query

from app.models.schemas import GenLayerValidationRequest, GenLayerValidationResult
from app.services.genlayer_ai import list_validation_history, validate_and_store_signal

router = APIRouter()


@router.post("/genlayer/validate", response_model=GenLayerValidationResult)
async def validate_signal(payload: GenLayerValidationRequest):
    return validate_and_store_signal(payload.signal)


@router.get("/genlayer/history", response_model=list[GenLayerValidationResult])
async def genlayer_history(limit: int = Query(default=50, ge=1, le=250)):
    return list_validation_history(limit=limit)
