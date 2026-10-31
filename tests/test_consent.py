"""Tests for Explicit Consent and Step-Up Escalation.

Verifies:
- WYSIWYS Preview generation and deterministic SHA-256 fingerprinting
- Ephemeral Escalation ticket generation and verification
- Anti-replay enforcement
- Parameter mismatch rejection (payee, amount)
- Expiry handling (> 60s)
"""

from __future__ import annotations

import time
import pytest

from kavach.consent import (
    EscalationChallenge,
    generate_mandate_preview,
    issue_escalation_ticket,
    verify_escalation_ticket,
)
from kavach.issuer import generate_keypair
from kavach.models import MandateSpec


@pytest.fixture
def user_keys():
    return generate_keypair()


class TestConsentAndPreview:
    """Tests for WYSIWYS Preview and Fingerprinting."""

    def test_mandate_preview_formatting_and_fingerprint(self):
        spec = MandateSpec(
            purpose="Monthly Groceries",
            allowed_payees=["ondc-grocer@upi"],
            allowed_categories=["groceries"],
            per_txn_limit_paise=150_000,
            total_budget_paise=300_000,
            max_txn_count=5,
        )

        preview = generate_mandate_preview(spec)

        assert preview.purpose == "Monthly Groceries"
        assert preview.per_txn_limit_inr == "₹1,500.00"
        assert preview.total_budget_inr == "₹3,000.00"
        assert preview.max_txn_count == 5
        assert len(preview.sha256_fingerprint) == 64

        # Fingerprint is deterministic
        preview2 = generate_mandate_preview(spec)
        assert preview.sha256_fingerprint == preview2.sha256_fingerprint


class TestStepUpEscalation:
    """Tests for Dynamic Step-Up Consent Tickets."""

    def test_valid_step_up_consent_flow(self, user_keys):
        user_priv, user_pub = user_keys
        used_tickets: set[str] = set()

        challenge = EscalationChallenge(
            challenge_id="chal-001",
            amount_paise=250_000,  # ₹2,500
            payee_vpa="ondc-grocer@upi",
            reason="Amount exceeds automated approval threshold (₹1,000)",
            exp=int(time.time()) + 60,
        )

        ticket = issue_escalation_ticket(user_priv, challenge, validity_seconds=60)

        ok, msg = verify_escalation_ticket(
            ticket=ticket,
            user_public_key=user_pub,
            expected_payee="ondc-grocer@upi",
            expected_amount_paise=250_000,
            used_ticket_ids=used_tickets,
        )
        assert ok is True
        assert "verified" in msg

        # Single-use: Replaying the same ticket fails
        replay_ok, replay_msg = verify_escalation_ticket(
            ticket=ticket,
            user_public_key=user_pub,
            expected_payee="ondc-grocer@upi",
            expected_amount_paise=250_000,
            used_ticket_ids=used_tickets,
        )
        assert replay_ok is False
        assert "already used" in replay_msg

    def test_payee_tampering_rejected(self, user_keys):
        user_priv, user_pub = user_keys
        used_tickets: set[str] = set()

        challenge = EscalationChallenge(
            challenge_id="chal-002",
            amount_paise=100_000,
            payee_vpa="ondc-grocer@upi",
            reason="Large purchase",
            exp=int(time.time()) + 60,
        )

        ticket = issue_escalation_ticket(user_priv, challenge)

        # Attacker tries to use ticket to pay someone else
        ok, msg = verify_escalation_ticket(
            ticket=ticket,
            user_public_key=user_pub,
            expected_payee="attacker@upi",
            expected_amount_paise=100_000,
            used_ticket_ids=used_tickets,
        )
        assert ok is False
        assert "payee mismatch" in msg

    def test_amount_tampering_rejected(self, user_keys):
        user_priv, user_pub = user_keys
        used_tickets: set[str] = set()

        challenge = EscalationChallenge(
            challenge_id="chal-003",
            amount_paise=100_000,
            payee_vpa="ondc-grocer@upi",
            reason="Large purchase",
            exp=int(time.time()) + 60,
        )

        ticket = issue_escalation_ticket(user_priv, challenge)

        # Attacker tries to raise amount
        ok, msg = verify_escalation_ticket(
            ticket=ticket,
            user_public_key=user_pub,
            expected_payee="ondc-grocer@upi",
            expected_amount_paise=500_000,
            used_ticket_ids=used_tickets,
        )
        assert ok is False
        assert "amount mismatch" in msg

    def test_expired_ticket_rejected(self, user_keys):
        user_priv, user_pub = user_keys
        used_tickets: set[str] = set()

        challenge = EscalationChallenge(
            challenge_id="chal-004",
            amount_paise=100_000,
            payee_vpa="ondc-grocer@upi",
            reason="Large purchase",
            exp=int(time.time()) - 10,
        )

        # Issued with negative validity (already expired)
        ticket = issue_escalation_ticket(user_priv, challenge, validity_seconds=-5)

        ok, msg = verify_escalation_ticket(
            ticket=ticket,
            user_public_key=user_pub,
            expected_payee="ondc-grocer@upi",
            expected_amount_paise=100_000,
            used_ticket_ids=used_tickets,
        )
        assert ok is False
        assert "Signature has expired" in msg or "invalid" in msg
