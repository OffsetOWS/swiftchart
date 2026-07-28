from typing import Literal

from pydantic import BaseModel, Field, field_validator


class PaymentSubmissionCreate(BaseModel):
    plan: Literal["pro_monthly", "pro_lifetime"] = "pro_monthly"
    transaction_hash: str = Field(min_length=66, max_length=66)
    sender_wallet: str | None = Field(default=None, max_length=128)

    @field_validator("transaction_hash")
    @classmethod
    def validate_transaction_hash(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized.startswith("0x") or len(normalized) != 66:
            raise ValueError("Enter a valid Base transaction hash.")
        try:
            int(normalized[2:], 16)
        except ValueError as exc:
            raise ValueError("Enter a valid Base transaction hash.") from exc
        return normalized

    @field_validator("sender_wallet")
    @classmethod
    def normalize_sender_wallet(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None


class PaymentReviewRequest(BaseModel):
    status: Literal["approved", "rejected"]
    rejection_reason: str | None = Field(default=None, max_length=500)

    @field_validator("rejection_reason")
    @classmethod
    def normalize_rejection_reason(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None
