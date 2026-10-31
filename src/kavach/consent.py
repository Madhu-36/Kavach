"""Kavach Explicit Consent & Step-Up Escalation (ESCALATE).

Implements:
1. "What You See Is What You Sign" (WYSIWYS) Mandate Preview:
   Generates a clear human-readable consent sheet with rupee formatting
   and SHA-256 fingerprint before digital signing.
2. Dynamic Step-Up Escalation:
   When an in-mandate payment exceeds `step_up_threshold_paise` (e.g. ₹1,000),
   the interceptor halts and returns an ESCALATE challenge.
   The transaction requires out-of-band user consent (simulating UPI PIN / Biometrics)
   producing an Ed25519-signed ephemeral EscalationTicket (valid 60 seconds).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Optional

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict

from kavach.models import Decision, MandateSpec, PaymentRequest, ReasonCode


class MandatePreview(BaseModel):
    """Human-readable preview of mandate claims with cryptographic fingerprint."""

    purpose: str
    allowed_payees: list[str]
    allowed_categories: list[str]
    per_txn_limit_inr: str
    total_budget_inr: str
    max_txn_count: int
    sha256_fingerprint: str

    model_config = ConfigDict(extra="forbid")


def generate_mandate_preview(spec: MandateSpec) -> MandatePreview:
    """Generate unambiguous human-readable preview before user signs."""
    canonical_json = json.dumps(spec.model_dump(), sort_keys=True)
    fingerprint = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    return MandatePreview(
        purpose=spec.purpose,
        allowed_payees=spec.allowed_payees,
        allowed_categories=spec.allowed_categories,
        per_txn_limit_inr=f"₹{spec.per_txn_limit_paise / 100:,.2f}",
        total_budget_inr=f"₹{spec.total_budget_paise / 100:,.2f}",
        max_txn_count=spec.max_txn_count,
        sha256_fingerprint=fingerprint,
    )


class EscalationChallenge(BaseModel):
    """Challenge issued when step-up consent is required."""

    challenge_id: str
    amount_paise: int
    payee_vpa: str
    reason: str
    exp: int

    model_config = ConfigDict(extra="forbid")


def issue_escalation_ticket(
    user_private_key: Ed25519PrivateKey,
    challenge: EscalationChallenge,
    validity_seconds: int = 60,
) -> str:
    """User authorizes step-up exception with personal private key (PIN simulation)."""
    now = int(time.time())
    payload = {
        "iss": "user-consent-authority",
        "sub": challenge.challenge_id,
        "jti": str(uuid.uuid4()),
        "amount_paise": challenge.amount_paise,
        "payee_vpa": challenge.payee_vpa,
        "iat": now,
        "exp": now + validity_seconds,
    }
    return jwt.encode(payload, user_private_key, algorithm="EdDSA")


def verify_escalation_ticket(
    ticket: str,
    user_public_key: Ed25519PublicKey,
    expected_payee: str,
    expected_amount_paise: int,
    used_ticket_ids: set[str],
) -> tuple[bool, str]:
    """Verify ephemeral escalation ticket and prevent replay."""
    try:
        payload = jwt.decode(
            ticket,
            user_public_key,
            algorithms=["EdDSA"],
            options={"require": ["iss", "sub", "jti", "exp", "amount_paise", "payee_vpa"]},
        )
    except Exception as exc:
        return False, f"Escalation ticket invalid: {exc}"

    # Single-use check
    ticket_id = payload["jti"]
    if ticket_id in used_ticket_ids:
        return False, "Escalation ticket already used (replay rejected)"

    # Parameter binding checks
    if payload["payee_vpa"] != expected_payee:
        return False, f"Escalation ticket payee mismatch: expected {expected_payee}, got {payload['payee_vpa']}"

    if payload["amount_paise"] != expected_amount_paise:
        return False, f"Escalation ticket amount mismatch: expected {expected_amount_paise}, got {payload['amount_paise']}"

    used_ticket_ids.add(ticket_id)
    return True, "User step-up consent verified ✓"
