from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from app.config import Settings, get_settings


PAYMENT_PLANS = {
    "pro_monthly": Decimal("9.99"),
    "pro_lifetime": Decimal("99.99"),
}


class PaymentServiceError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str


class SupabasePaymentService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _require_configuration(self) -> None:
        if not (
            self.settings.supabase_url
            and self.settings.supabase_service_role_key
        ):
            raise PaymentServiceError(503, "Payment review is temporarily unavailable.")

    @property
    def _base_url(self) -> str:
        return self.settings.supabase_url.rstrip("/")

    def _service_headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.settings.supabase_service_role_key,
            "Authorization": f"Bearer {self.settings.supabase_service_role_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    async def authenticate(self, access_token: str) -> AuthenticatedUser:
        self._require_configuration()
        if not access_token:
            raise PaymentServiceError(401, "Sign in to continue.")

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self._base_url}/auth/v1/user",
                headers={
                    "apikey": (
                        self.settings.supabase_anon_key
                        or self.settings.supabase_service_role_key
                    ),
                    "Authorization": f"Bearer {access_token}",
                },
            )

        if response.status_code != 200:
            raise PaymentServiceError(401, "Your session has expired. Sign in again.")
        payload = response.json()
        user_id = str(payload.get("id") or "")
        email = str(payload.get("email") or "").strip().lower()
        if not user_id or not email:
            raise PaymentServiceError(401, "Your authenticated account could not be verified.")
        return AuthenticatedUser(id=user_id, email=email)

    async def is_admin(self, user: AuthenticatedUser) -> bool:
        self._require_configuration()
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self._base_url}/rest/v1/payment_admins",
                params={"select": "email", "email": f"ilike.{user.email}", "limit": "1"},
                headers=self._service_headers(),
            )
        if response.status_code != 200:
            raise PaymentServiceError(503, "Could not verify payment administrator access.")
        return bool(response.json())

    async def submit(
        self,
        user: AuthenticatedUser,
        *,
        plan: str,
        transaction_hash: str,
        sender_wallet: str | None,
    ) -> dict[str, Any]:
        self._require_configuration()
        amount = PAYMENT_PLANS.get(plan)
        if amount is None:
            raise PaymentServiceError(422, "Unsupported payment plan.")

        body = {
            "user_id": user.id,
            "email": user.email,
            "plan": plan,
            "plan_requested": plan,
            "network": "Base",
            "token": "USDC",
            "expected_amount": str(amount),
            "amount": str(amount),
            "transaction_hash": transaction_hash,
            "tx_hash": transaction_hash,
            "sender_wallet": sender_wallet,
            "status": "pending",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{self._base_url}/rest/v1/payment_submissions",
                params={
                    "select": (
                        "id,user_id,email,plan,network,token,expected_amount,"
                        "transaction_hash,sender_wallet,status,submitted_at"
                    )
                },
                headers=self._service_headers("return=representation"),
                json=body,
            )

        if response.status_code == 409:
            raise PaymentServiceError(409, "This transaction hash has already been submitted.")
        if response.status_code not in (200, 201):
            raise PaymentServiceError(503, "Could not save this payment submission.")
        rows = response.json()
        if not rows:
            raise PaymentServiceError(503, "Could not save this payment submission.")
        return rows[0]

    async def list_for_user(self, user: AuthenticatedUser) -> list[dict[str, Any]]:
        self._require_configuration()
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self._base_url}/rest/v1/payment_submissions",
                params={
                    "select": (
                        "id,user_id,plan,network,token,expected_amount,transaction_hash,"
                        "sender_wallet,status,rejection_reason,submitted_at,reviewed_at,"
                        "payment_confirmed_at"
                    ),
                    "user_id": f"eq.{user.id}",
                    "order": "submitted_at.desc",
                },
                headers=self._service_headers(),
            )
        if response.status_code != 200:
            raise PaymentServiceError(503, "Could not load payment status.")
        return response.json()

    async def list_pending(self, admin: AuthenticatedUser) -> list[dict[str, Any]]:
        if not await self.is_admin(admin):
            raise PaymentServiceError(403, "Admin access required.")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self._base_url}/rest/v1/payment_submissions",
                params={
                    "select": (
                        "id,user_id,email,plan,network,token,expected_amount,"
                        "transaction_hash,sender_wallet,status,submitted_at"
                    ),
                    "status": "eq.pending",
                    "order": "submitted_at.asc",
                },
                headers=self._service_headers(),
            )
        if response.status_code != 200:
            raise PaymentServiceError(503, "Could not load payment submissions.")
        return response.json()

    async def review(
        self,
        admin: AuthenticatedUser,
        *,
        submission_id: str,
        status: str,
        rejection_reason: str | None,
    ) -> dict[str, Any]:
        if not await self.is_admin(admin):
            raise PaymentServiceError(403, "Admin access required.")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{self._base_url}/rest/v1/rpc/review_payment_submission_backend",
                headers=self._service_headers(),
                json={
                    "p_submission_id": submission_id,
                    "p_status": status,
                    "p_rejection_reason": rejection_reason,
                    "p_reviewer_id": admin.id,
                },
            )
        if response.status_code == 404:
            raise PaymentServiceError(404, "Pending payment submission not found.")
        if response.status_code not in (200, 201):
            detail = response.json().get("message", "") if response.content else ""
            if "Pending payment submission not found" in detail:
                raise PaymentServiceError(404, "Pending payment submission not found.")
            raise PaymentServiceError(503, "Could not review this payment submission.")
        payload = response.json()
        return payload[0] if isinstance(payload, list) and payload else payload
