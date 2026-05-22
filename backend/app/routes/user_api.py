from __future__ import annotations

from fastapi import APIRouter, Depends

from app.models.api import TakeTradeRequest, TradeUpdateRequest, UserProfileResponse, UserTakenTrade
from app.utils.auth import CurrentUser, current_user
from app.services.unified_api import delete_user_trade, ensure_profile, list_user_trades, take_trade, update_user_trade

router = APIRouter()


@router.get("/user/profile", response_model=UserProfileResponse)
async def user_profile(user: CurrentUser = Depends(current_user)):
    profile, created = ensure_profile(user)
    return UserProfileResponse(id=profile["user_id"], email=profile.get("email"), created=created)


@router.get("/user/trades", response_model=list[UserTakenTrade])
async def user_trades(user: CurrentUser = Depends(current_user)):
    return list_user_trades(user)


@router.post("/user/take-trade", response_model=UserTakenTrade)
async def user_take_trade(payload: TakeTradeRequest, user: CurrentUser = Depends(current_user)):
    return take_trade(user, payload)


@router.patch("/user/trades/{trade_id}", response_model=UserTakenTrade)
async def user_update_trade(trade_id: int, payload: TradeUpdateRequest, user: CurrentUser = Depends(current_user)):
    return update_user_trade(user, trade_id, payload)


@router.delete("/user/trades/{trade_id}")
async def user_delete_trade(trade_id: int, user: CurrentUser = Depends(current_user)):
    return delete_user_trade(user, trade_id)
