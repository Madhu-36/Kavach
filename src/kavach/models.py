"""Pydantic models for Kavach — all money values in integer paise.
Hardened for Phase 2: extra='forbid', strict input validation, and signed approval tickets.
"""

from __future__ import annotations

import enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MandateSpec(BaseModel):
    """The `mandate` claim inside the JWT."""

    purpose: str
    allowed_payees: list[str] = Field(min_length=1)
    allowed_categories: list[str] = Field(default_factory=list)
    per_txn_limit_paise: int = Field(gt=0)
    total_budget_paise: int = Field(gt=0)
    max_txn_count: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid")


class MandateClaims(BaseModel):
    """Full decoded JWT payload."""

    iss: str
    sub: str  # agent_id
    jti: str  # unique mandate id
    iat: int
    nbf: int
    exp: int
    mandate: MandateSpec

    model_config = ConfigDict(extra="forbid")


class ReasonCode(str, enum.Enum):
    """Structured reason codes for interceptor decisions."""

    APPROVED = "APPROVED"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    ALGORITHM_NOT_ALLOWED = "ALGORITHM_NOT_ALLOWED"
    MANDATE_EXPIRED = "MANDATE_EXPIRED"
    MANDATE_NOT_YET_VALID = "MANDATE_NOT_YET_VALID"
    MANDATE_REVOKED = "MANDATE_REVOKED"
    PAYEE_NOT_ALLOWED = "PAYEE_NOT_ALLOWED"
    CATEGORY_NOT_ALLOWED = "CATEGORY_NOT_ALLOWED"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    AMOUNT_EXCEEDS_PER_TXN_LIMIT = "AMOUNT_EXCEEDS_PER_TXN_LIMIT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    TXN_COUNT_EXCEEDED = "TXN_COUNT_EXCEEDED"
    NONCE_REPLAYED = "NONCE_REPLAYED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    AUDIT_LOG_TAMPERED = "AUDIT_LOG_TAMPERED"
    APPROVAL_TICKET_INVALID = "APPROVAL_TICKET_INVALID"
    EXTRA_ARGUMENTS_FORBIDDEN = "EXTRA_ARGUMENTS_FORBIDDEN"


class Decision(BaseModel):
    """Result of an interceptor check."""

    allowed: bool
    reason_code: ReasonCode
    message: str
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    approval_ticket: Optional[str] = None  # Signed Ed25519 JWT ticket if approved

    model_config = ConfigDict(extra="forbid")

    @staticmethod
    def approve(
        message: str = "Payment approved", approval_ticket: Optional[str] = None
    ) -> Decision:
        return Decision(
            allowed=True,
            reason_code=ReasonCode.APPROVED,
            message=message,
            reason_codes=[ReasonCode.APPROVED],
            approval_ticket=approval_ticket,
        )

    @staticmethod
    def deny(reason_code: ReasonCode, message: str) -> Decision:
        return Decision(
            allowed=False,
            reason_code=reason_code,
            message=message,
            reason_codes=[reason_code],
        )

    @staticmethod
    def deny_multi(reason_codes: list[ReasonCode], message: str) -> Decision:
        primary = reason_codes[0] if reason_codes else ReasonCode.INTERNAL_ERROR
        return Decision(
            allowed=False,
            reason_code=primary,
            message=message,
            reason_codes=reason_codes,
        )


class Product(BaseModel):
    """A product listing from the ONDC store."""

    name: str
    price_paise: int = Field(gt=0)
    category: str
    seller_vpa: str
    description: str = ""

    model_config = ConfigDict(extra="forbid")


class PaymentRequest(BaseModel):
    """A payment request evaluated by the interceptor."""

    amount_paise: int
    payee_vpa: str
    note: str = ""
    category: str = ""
    nonce: str = ""  # unique per-payment nonce for replay protection
    idempotency_key: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class MakePaymentInput(BaseModel):
    """Strict schema for the agent's make_upi_payment tool.

    Rejects any unexpected or override arguments.
    """

    amount_paise: int
    payee_vpa: str
    note: str = ""
    category: str = "groceries"

    model_config = ConfigDict(extra="forbid")

    @field_validator("amount_paise", mode="before")
    @classmethod
    def validate_amount_strict(cls, v: object) -> int:
        if isinstance(v, bool):
            raise ValueError("Boolean values are not valid payment amounts.")
        if isinstance(v, float):
            raise ValueError("Floating point amounts are not allowed. Provide integer paise.")
        if not isinstance(v, int):
            raise ValueError(f"Amount must be an integer, got {type(v).__name__}")
        if v <= 0:
            raise ValueError("Amount must be greater than 0 paise.")
        if v > 1_000_000_000:  # 10 million rupees cap sanity check
            raise ValueError("Amount exceeds maximum supported currency bounds.")
        return v


class ApprovalTicketPayload(BaseModel):
    """Claims inside the interceptor's signed approval ticket."""

    iss: str = "kavach-interceptor"
    sub: str  # mandate_jti
    jti: str  # unique ticket_id
    payee_vpa: str
    amount_paise: int
    nonce: str
    iat: int
    exp: int

    model_config = ConfigDict(extra="forbid")


class PaymentResult(BaseModel):
    """Result returned to the agent."""

    success: bool
    message: str
    txn_id: Optional[str] = None
    balance_paise: Optional[int] = None

    model_config = ConfigDict(extra="forbid")


class AuditEntry(BaseModel):
    """An entry in the hash-chained audit log."""

    seq: int
    timestamp: str
    mandate_jti: str
    payment: PaymentRequest
    decision: Decision
    entry_hash: str
    prev_hash: str
    approval_ticket_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")
