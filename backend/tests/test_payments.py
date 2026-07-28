from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.routes.payments import payment_service
from app.services.payments import AuthenticatedUser, PaymentServiceError


VALID_HASH = "0x" + ("a" * 64)


class FakePaymentService:
    def __init__(self):
        self.admin = True
        self.duplicate = False
        self.reviewed = []

    async def authenticate(self, access_token):
        if access_token != "valid-token":
            raise PaymentServiceError(401, "Your session has expired. Sign in again.")
        return AuthenticatedUser(id="user-123", email="trader@example.com")

    async def submit(self, user, *, plan, transaction_hash, sender_wallet):
        if self.duplicate:
            raise PaymentServiceError(409, "This transaction hash has already been submitted.")
        return {
            "id": "payment-123",
            "user_id": user.id,
            "email": user.email,
            "plan": plan,
            "expected_amount": "9.99",
            "network": "Base",
            "token": "USDC",
            "transaction_hash": transaction_hash,
            "sender_wallet": sender_wallet,
            "status": "pending",
            "submitted_at": "2026-07-28T00:00:00Z",
        }

    async def list_for_user(self, user):
        return [{"id": "payment-123", "user_id": user.id, "status": "pending"}]

    async def is_admin(self, user):
        return self.admin

    async def list_pending(self, user):
        if not self.admin:
            raise PaymentServiceError(403, "Admin access required.")
        return [{"id": "payment-123", "email": "trader@example.com", "status": "pending"}]

    async def review(self, user, *, submission_id, status, rejection_reason):
        if not self.admin:
            raise PaymentServiceError(403, "Admin access required.")
        self.reviewed.append((user.id, submission_id, status, rejection_reason))
        return {"id": submission_id, "status": status}


def auth_headers():
    return {"Authorization": "Bearer valid-token"}


def test_payment_submission_requires_authenticated_session():
    client = TestClient(app)
    response = client.post(
        "/api/payments/submissions",
        json={"plan": "pro_monthly", "transaction_hash": VALID_HASH},
    )
    assert response.status_code == 401


def test_payment_submission_uses_authenticated_identity():
    service = FakePaymentService()
    app.dependency_overrides[payment_service] = lambda: service
    try:
        client = TestClient(app)
        response = client.post(
            "/api/payments/submissions",
            headers=auth_headers(),
            json={"plan": "pro_monthly", "transaction_hash": VALID_HASH},
        )
        assert response.status_code == 201
        assert response.json()["user_id"] == "user-123"
        assert response.json()["email"] == "trader@example.com"
        assert response.json()["expected_amount"] == "9.99"
    finally:
        app.dependency_overrides.clear()


def test_duplicate_transaction_hash_returns_conflict():
    service = FakePaymentService()
    service.duplicate = True
    app.dependency_overrides[payment_service] = lambda: service
    try:
        client = TestClient(app)
        response = client.post(
            "/api/payments/submissions",
            headers=auth_headers(),
            json={"plan": "pro_monthly", "transaction_hash": VALID_HASH},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "This transaction hash has already been submitted."
    finally:
        app.dependency_overrides.clear()


def test_invalid_transaction_hash_is_rejected():
    service = FakePaymentService()
    app.dependency_overrides[payment_service] = lambda: service
    try:
        client = TestClient(app)
        response = client.post(
            "/api/payments/submissions",
            headers=auth_headers(),
            json={"plan": "pro_monthly", "transaction_hash": "0x123"},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_non_admin_cannot_review_payment():
    service = FakePaymentService()
    service.admin = False
    app.dependency_overrides[payment_service] = lambda: service
    try:
        client = TestClient(app)
        response = client.post(
            "/api/payments/admin/submissions/payment-123/review",
            headers=auth_headers(),
            json={"status": "approved"},
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_can_approve_payment():
    service = FakePaymentService()
    app.dependency_overrides[payment_service] = lambda: service
    try:
        client = TestClient(app)
        response = client.post(
            "/api/payments/admin/submissions/payment-123/review",
            headers=auth_headers(),
            json={"status": "approved"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "approved"
        assert service.reviewed == [
            ("user-123", "payment-123", "approved", None),
        ]
    finally:
        app.dependency_overrides.clear()


def test_payment_migration_enforces_secure_monthly_activation():
    migration = (
        Path(__file__).parents[2] / "supabase" / "payment_submissions.sql"
    ).read_text()
    assert "lower(transaction_hash)" in migration
    assert "revoke all on public.payment_submissions from anon, authenticated" in migration
    assert "revoke insert on public.profiles from authenticated" in migration
    assert "grant execute on function public.review_payment_submission_backend" in migration
    assert "to service_role" in migration
    assert "now() + interval '30 days'" in migration
    assert "subscription_expires_at" in migration
    assert "Administrators cannot review their own payment" in migration
