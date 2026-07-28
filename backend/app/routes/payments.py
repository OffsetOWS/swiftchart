from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from app.models.payments import PaymentReviewRequest, PaymentSubmissionCreate
from app.services.payments import (
    AuthenticatedUser,
    PaymentServiceError,
    SupabasePaymentService,
)

router = APIRouter()


def payment_service() -> SupabasePaymentService:
    return SupabasePaymentService()


async def authenticated_user(
    authorization: Annotated[str | None, Header()] = None,
    service: SupabasePaymentService = Depends(payment_service),
) -> AuthenticatedUser:
    scheme, _, token = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    try:
        return await service.authenticate(token.strip())
    except PaymentServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def handle_service_error(exc: PaymentServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post("/payments/submissions", status_code=201)
async def create_payment_submission(
    payload: PaymentSubmissionCreate,
    user: AuthenticatedUser = Depends(authenticated_user),
    service: SupabasePaymentService = Depends(payment_service),
):
    try:
        return await service.submit(
            user,
            plan=payload.plan,
            transaction_hash=payload.transaction_hash,
            sender_wallet=payload.sender_wallet,
        )
    except PaymentServiceError as exc:
        raise handle_service_error(exc) from exc


@router.get("/payments/submissions/me")
async def my_payment_submissions(
    user: AuthenticatedUser = Depends(authenticated_user),
    service: SupabasePaymentService = Depends(payment_service),
):
    try:
        return await service.list_for_user(user)
    except PaymentServiceError as exc:
        raise handle_service_error(exc) from exc


@router.get("/payments/admin/access")
async def payment_admin_access(
    user: AuthenticatedUser = Depends(authenticated_user),
    service: SupabasePaymentService = Depends(payment_service),
):
    try:
        return {"is_admin": await service.is_admin(user)}
    except PaymentServiceError as exc:
        raise handle_service_error(exc) from exc


@router.get("/payments/admin/submissions")
async def pending_payment_submissions(
    user: AuthenticatedUser = Depends(authenticated_user),
    service: SupabasePaymentService = Depends(payment_service),
):
    try:
        return await service.list_pending(user)
    except PaymentServiceError as exc:
        raise handle_service_error(exc) from exc


@router.post("/payments/admin/submissions/{submission_id}/review")
async def review_payment_submission(
    submission_id: str,
    payload: PaymentReviewRequest,
    user: AuthenticatedUser = Depends(authenticated_user),
    service: SupabasePaymentService = Depends(payment_service),
):
    try:
        return await service.review(
            user,
            submission_id=submission_id,
            status=payload.status,
            rejection_reason=payload.rejection_reason,
        )
    except PaymentServiceError as exc:
        raise handle_service_error(exc) from exc
