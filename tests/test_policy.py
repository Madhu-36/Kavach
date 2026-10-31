"""Tests for policy checks and interceptor — covers all 17+ attack vectors."""

from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kavach.interceptor import KavachInterceptor
from kavach.issuer import generate_keypair, public_key_to_pem, sign_mandate
from kavach.ledger import Ledger
from kavach.audit import AuditLog
from kavach.models import Decision, MandateSpec, PaymentRequest, ReasonCode
from kavach.policy import (
    check_amount,
    check_budget,
    check_category,
    check_payee,
    check_per_txn_limit,
    check_replay,
    check_revocation,
    check_signature_and_algorithm,
    check_time_window,
    check_txn_count,
)


# =====================================================================
# 1. Valid payment (happy path)
# =====================================================================

class TestValidPayment:
    def test_valid_payment_approved(self, interceptor):
        req = PaymentRequest(
            amount_paise=120000,
            payee_vpa="ondc-grocer@upi",
            note="Rice and oil",
            category="groceries",
            nonce="nonce-001",
        )
        decision, claims = interceptor.evaluate(req)
        assert decision.allowed is True
        assert decision.reason_code == ReasonCode.APPROVED


# =====================================================================
# 2. Amount over per-txn limit
# =====================================================================

class TestPerTxnLimit:
    def test_amount_over_per_txn_limit(self, interceptor):
        req = PaymentRequest(
            amount_paise=200000,  # ₹2,000 > ₹1,500 limit
            payee_vpa="ondc-grocer@upi",
            category="groceries",
            nonce="nonce-002",
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False
        assert ReasonCode.AMOUNT_EXCEEDS_PER_TXN_LIMIT in (
            [decision.reason_code] + decision.reason_codes
        )


# =====================================================================
# 3. Wrong payee
# =====================================================================

class TestWrongPayee:
    def test_wrong_payee_rejected(self, interceptor):
        req = PaymentRequest(
            amount_paise=100000,
            payee_vpa="attacker_wallet@upi",
            category="groceries",
            nonce="nonce-003",
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False
        assert ReasonCode.PAYEE_NOT_ALLOWED in (
            [decision.reason_code] + decision.reason_codes
        )


# =====================================================================
# 4. Expired token
# =====================================================================

class TestExpiredToken:
    def test_expired_token_rejected(self, private_key, public_key, default_mandate, tmp_path):
        token = sign_mandate(
            private_key=private_key,
            agent_id="agent_001",
            mandate=default_mandate,
            validity_seconds=-10,  # already expired
        )
        interceptor = KavachInterceptor(
            public_key=public_key,
            mandate_token=token,
            ledger=Ledger(),
            audit_log=AuditLog(),
        )
        req = PaymentRequest(
            amount_paise=100000, payee_vpa="ondc-grocer@upi", category="groceries"
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False
        assert decision.reason_code == ReasonCode.MANDATE_EXPIRED


# =====================================================================
# 5. Token not yet valid
# =====================================================================

class TestNotYetValid:
    def test_token_not_yet_valid(self, private_key, public_key, default_mandate):
        token = sign_mandate(
            private_key=private_key,
            agent_id="agent_001",
            mandate=default_mandate,
            validity_seconds=900,
            nbf_offset=3600,  # nbf = 1 hour from now
        )
        interceptor = KavachInterceptor(
            public_key=public_key,
            mandate_token=token,
            ledger=Ledger(),
            audit_log=AuditLog(),
        )
        req = PaymentRequest(
            amount_paise=100000, payee_vpa="ondc-grocer@upi", category="groceries"
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False
        assert decision.reason_code == ReasonCode.MANDATE_NOT_YET_VALID


# =====================================================================
# 6. Tampered payload
# =====================================================================

class TestTamperedPayload:
    def test_tampered_payload_rejected(self, private_key, public_key, default_mandate):
        token = sign_mandate(
            private_key=private_key,
            agent_id="agent_001",
            mandate=default_mandate,
        )
        # Tamper with the payload: change per_txn_limit by modifying the base64
        parts = token.split(".")
        import base64

        # Decode payload, modify, re-encode
        # Add padding
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_bytes)
        payload["mandate"]["per_txn_limit_paise"] = 99999999  # tamper!
        new_payload = base64.urlsafe_b64encode(
            json.dumps(payload).encode()
        ).rstrip(b"=").decode()
        tampered_token = f"{parts[0]}.{new_payload}.{parts[2]}"

        interceptor = KavachInterceptor(
            public_key=public_key,
            mandate_token=tampered_token,
            ledger=Ledger(),
            audit_log=AuditLog(),
        )
        req = PaymentRequest(
            amount_paise=100000, payee_vpa="ondc-grocer@upi", category="groceries"
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False
        assert decision.reason_code == ReasonCode.SIGNATURE_INVALID


# =====================================================================
# 7. Wrong signing key
# =====================================================================

class TestWrongKey:
    def test_wrong_key_rejected(self, public_key, default_mandate):
        # Sign with a DIFFERENT private key
        other_priv, _ = generate_keypair()
        token = sign_mandate(
            private_key=other_priv,
            agent_id="agent_001",
            mandate=default_mandate,
        )
        interceptor = KavachInterceptor(
            public_key=public_key,
            mandate_token=token,
            ledger=Ledger(),
            audit_log=AuditLog(),
        )
        req = PaymentRequest(
            amount_paise=100000, payee_vpa="ondc-grocer@upi", category="groceries"
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False
        assert decision.reason_code == ReasonCode.SIGNATURE_INVALID


# =====================================================================
# 8. alg=none token
# =====================================================================

class TestAlgNone:
    def test_alg_none_rejected(self, public_key, default_mandate):
        # Craft a token with alg=none
        import base64

        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        payload_data = {
            "iss": "user@kavach",
            "sub": "agent_001",
            "jti": "fake-jti",
            "iat": int(time.time()),
            "nbf": int(time.time()),
            "exp": int(time.time()) + 900,
            "mandate": default_mandate.model_dump(),
        }
        payload = base64.urlsafe_b64encode(
            json.dumps(payload_data).encode()
        ).rstrip(b"=").decode()
        none_token = f"{header}.{payload}."

        interceptor = KavachInterceptor(
            public_key=public_key,
            mandate_token=none_token,
            ledger=Ledger(),
            audit_log=AuditLog(),
        )
        req = PaymentRequest(
            amount_paise=100000, payee_vpa="ondc-grocer@upi", category="groceries"
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False
        assert decision.reason_code == ReasonCode.ALGORITHM_NOT_ALLOWED


# =====================================================================
# 9. HS256 token signed with the public key (algorithm confusion attack)
# =====================================================================

class TestAlgConfusion:
    def test_hs256_with_public_key_rejected(self, public_key, default_mandate):
        """Algorithm confusion attack: craft an HS256 token using raw public key bytes.

        Modern PyJWT refuses to use PEM as HMAC secret, so a real attacker
        would use the raw 32-byte public key or craft the token manually.
        We build the token with raw HMAC to simulate this.
        """
        import base64
        import hashlib
        import hmac

        # Get raw 32-byte public key
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        raw_pub = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)

        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()

        payload_data = {
            "iss": "user@kavach",
            "sub": "agent_001",
            "jti": "fake-jti",
            "iat": int(time.time()),
            "nbf": int(time.time()),
            "exp": int(time.time()) + 900,
            "mandate": default_mandate.model_dump(),
        }
        payload = base64.urlsafe_b64encode(
            json.dumps(payload_data).encode()
        ).rstrip(b"=").decode()

        signing_input = f"{header}.{payload}".encode()
        sig = hmac.new(raw_pub, signing_input, hashlib.sha256).digest()
        signature = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()

        confused_token = f"{header}.{payload}.{signature}"

        interceptor = KavachInterceptor(
            public_key=public_key,
            mandate_token=confused_token,
            ledger=Ledger(),
            audit_log=AuditLog(),
        )
        req = PaymentRequest(
            amount_paise=100000, payee_vpa="ondc-grocer@upi", category="groceries"
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False
        assert decision.reason_code == ReasonCode.ALGORITHM_NOT_ALLOWED


# =====================================================================
# 10. Cumulative budget exhaustion across multiple payments
# =====================================================================

class TestBudgetExhaustion:
    def test_cumulative_budget_exceeded(self, interceptor):
        # Budget is 300000 paise (₹3,000). Make 3 payments of ₹1,000.
        for i in range(3):
            req = PaymentRequest(
                amount_paise=100000,
                payee_vpa="ondc-grocer@upi",
                category="groceries",
                nonce=f"budget-nonce-{i}",
            )
            decision, _ = interceptor.evaluate(req)
            assert decision.allowed is True, f"Payment {i} should have been approved"

        # 4th payment should fail (would be 400000 > 300000)
        req = PaymentRequest(
            amount_paise=100000,
            payee_vpa="ondc-grocer@upi",
            category="groceries",
            nonce="budget-nonce-3",
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False
        assert ReasonCode.BUDGET_EXHAUSTED in (
            [decision.reason_code] + decision.reason_codes
        )


# =====================================================================
# 11. Transaction count limit
# =====================================================================

class TestTxnCountLimit:
    def test_txn_count_exceeded(self, private_key, public_key, tmp_path):
        mandate = MandateSpec(
            purpose="test",
            allowed_payees=["ondc-grocer@upi"],
            allowed_categories=["groceries"],
            per_txn_limit_paise=1000000,
            total_budget_paise=10000000,
            max_txn_count=2,  # Only 2 transactions allowed
        )
        token = sign_mandate(private_key, "agent_001", mandate)
        interceptor = KavachInterceptor(
            public_key=public_key,
            mandate_token=token,
            ledger=Ledger(),
            audit_log=AuditLog(),
        )

        for i in range(2):
            req = PaymentRequest(
                amount_paise=1000,
                payee_vpa="ondc-grocer@upi",
                category="groceries",
                nonce=f"count-nonce-{i}",
            )
            decision, _ = interceptor.evaluate(req)
            assert decision.allowed is True

        # 3rd should fail
        req = PaymentRequest(
            amount_paise=1000,
            payee_vpa="ondc-grocer@upi",
            category="groceries",
            nonce="count-nonce-2",
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False
        assert ReasonCode.TXN_COUNT_EXCEEDED in (
            [decision.reason_code] + decision.reason_codes
        )


# =====================================================================
# 12. Replayed jti/nonce
# =====================================================================

class TestReplay:
    def test_replayed_nonce_rejected(self, interceptor):
        req = PaymentRequest(
            amount_paise=50000,
            payee_vpa="ondc-grocer@upi",
            category="groceries",
            nonce="unique-nonce-replay",
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is True

        # Replay same nonce
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False
        assert ReasonCode.NONCE_REPLAYED in (
            [decision.reason_code] + decision.reason_codes
        )


# =====================================================================
# 13. Revoked mandate
# =====================================================================

class TestRevocation:
    def test_revoked_mandate_rejected(
        self, private_key, public_key, default_mandate
    ):
        token = sign_mandate(
            private_key=private_key,
            agent_id="agent_001",
            mandate=default_mandate,
            jti="revocable-mandate-001",
        )
        interceptor = KavachInterceptor(
            public_key=public_key,
            mandate_token=token,
            ledger=Ledger(),
            audit_log=AuditLog(),
            revoked_jtis=frozenset({"revocable-mandate-001"}),
        )
        req = PaymentRequest(
            amount_paise=100000, payee_vpa="ondc-grocer@upi", category="groceries"
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False
        assert decision.reason_code == ReasonCode.MANDATE_REVOKED


# =====================================================================
# 14. Zero and negative amounts
# =====================================================================

class TestInvalidAmounts:
    @pytest.mark.parametrize("amount", [0, -100, -1])
    def test_zero_and_negative_amounts_rejected(self, interceptor, amount):
        req = PaymentRequest(
            amount_paise=amount,
            payee_vpa="ondc-grocer@upi",
            category="groceries",
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False
        assert ReasonCode.INVALID_AMOUNT in (
            [decision.reason_code] + decision.reason_codes
        )


# =====================================================================
# 15. Paise rounding edge cases
# =====================================================================

class TestPaiseEdgeCases:
    def test_exact_per_txn_limit_allowed(self, interceptor):
        """Payment exactly at the per-txn limit should pass."""
        req = PaymentRequest(
            amount_paise=150000,  # Exactly ₹1,500 = limit
            payee_vpa="ondc-grocer@upi",
            category="groceries",
            nonce="exact-limit",
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is True

    def test_one_paisa_over_limit_rejected(self, interceptor):
        """One paisa over the limit should fail."""
        req = PaymentRequest(
            amount_paise=150001,  # ₹1,500.01
            payee_vpa="ondc-grocer@upi",
            category="groceries",
            nonce="one-over",
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False

    def test_one_paisa_payment(self, interceptor):
        """Minimum valid payment: 1 paisa."""
        req = PaymentRequest(
            amount_paise=1,
            payee_vpa="ondc-grocer@upi",
            category="groceries",
            nonce="one-paisa",
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is True

    def test_exact_budget_boundary(self, private_key, public_key):
        """Spending exactly the budget should succeed; one paisa more should fail."""
        mandate = MandateSpec(
            purpose="test",
            allowed_payees=["ondc-grocer@upi"],
            allowed_categories=["groceries"],
            per_txn_limit_paise=100000,
            total_budget_paise=100000,
            max_txn_count=5,
        )
        token = sign_mandate(private_key, "agent_001", mandate)
        interceptor = KavachInterceptor(
            public_key=public_key,
            mandate_token=token,
            ledger=Ledger(),
            audit_log=AuditLog(),
        )

        # Exactly the budget
        req = PaymentRequest(
            amount_paise=100000,
            payee_vpa="ondc-grocer@upi",
            category="groceries",
            nonce="exact-budget",
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is True

        # One paisa more (budget now exhausted)
        req2 = PaymentRequest(
            amount_paise=1,
            payee_vpa="ondc-grocer@upi",
            category="groceries",
            nonce="one-more",
        )
        decision2, _ = interceptor.evaluate(req2)
        assert decision2.allowed is False


# =====================================================================
# 16. Category check
# =====================================================================

class TestCategoryCheck:
    def test_wrong_category_rejected(self, interceptor):
        req = PaymentRequest(
            amount_paise=50000,
            payee_vpa="ondc-grocer@upi",
            category="electronics",
            nonce="cat-nonce",
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False
        assert ReasonCode.CATEGORY_NOT_ALLOWED in (
            [decision.reason_code] + decision.reason_codes
        )

    def test_correct_category_case_insensitive(self, interceptor):
        req = PaymentRequest(
            amount_paise=50000,
            payee_vpa="ondc-grocer@upi",
            category="GROCERIES",
            nonce="cat-case",
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is True


# =====================================================================
# 17. Multi-reason denial (payee + amount)
# =====================================================================

class TestMultiReasonDenial:
    def test_multiple_violations_reported(self, interceptor):
        """Attack payment: wrong payee AND over limit."""
        req = PaymentRequest(
            amount_paise=500000,  # ₹5,000 > ₹1,500 limit
            payee_vpa="attacker_wallet@upi",
            category="groceries",
            nonce="multi-fail",
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False
        all_codes = [decision.reason_code] + decision.reason_codes
        assert ReasonCode.PAYEE_NOT_ALLOWED in all_codes
        assert ReasonCode.AMOUNT_EXCEEDS_PER_TXN_LIMIT in all_codes
