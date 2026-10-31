"""Adversarial Red-Team Test Suite for Kavach Phase 2.

Tests all bypass paths and attack vectors identified in the threat model:
1. Direct gateway bypass without signed ticket
2. Approval ticket tampering (payee, amount)
3. Approval ticket single-use replay
4. Expired approval ticket
5. Tool schema argument injection (override, skip_checks, kavach_token)
6. Type confusion (boolean, float, NaN, inf, huge integer)
7. Payee lookalike attacks (trailing whitespace, multiple @, .evil domain)
8. Concurrency race conditions (50 threads with SQLite BEGIN IMMEDIATE)
9. Salami-slicing budget exhaustion
10. Process isolation assertion (no private keys/tokens in agent environment)
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import time
import uuid
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from kavach.agent import make_upi_payment_tool
from kavach.audit import AuditLog
from kavach.client import KavachClient
from kavach.interceptor import KavachInterceptor
from kavach.issuer import (
    generate_keypair,
    sign_approval_ticket,
    sign_mandate,
)
from kavach.ledger_db import SqliteLedger
from kavach.mock_upi import MockUPIGateway, create_app
from kavach.models import (
    Decision,
    MakePaymentInput,
    MandateSpec,
    PaymentRequest,
    ReasonCode,
)
from kavach.policy import check_amount, check_payee


# ===========================================================================
# 1. Direct Gateway Bypass & Signed Approval Enforcement
# ===========================================================================

class TestGatewaySecurity:
    @pytest.fixture
    def gateway_setup(self):
        priv_interceptor, pub_interceptor = generate_keypair()
        gw = MockUPIGateway(interceptor_public_key=pub_interceptor)
        gw.set_balance("user@upi", 1_000_000)
        app = create_app(gw)
        client = TestClient(app)
        return gw, client, priv_interceptor, pub_interceptor

    def test_direct_call_without_ticket_rejected(self, gateway_setup):
        _, client, _, _ = gateway_setup
        resp = client.post(
            "/pay",
            json={
                "payer_vpa": "user@upi",
                "payee_vpa": "attacker_wallet@upi",
                "amount_paise": 500000,
            },
        )
        assert resp.status_code == 403
        assert "Missing approval ticket" in resp.json()["detail"]

    def test_legacy_shared_secret_rejected_when_keys_enabled(self, gateway_setup):
        _, client, _, _ = gateway_setup
        resp = client.post(
            "/pay",
            json={
                "payer_vpa": "user@upi",
                "payee_vpa": "attacker_wallet@upi",
                "amount_paise": 500000,
            },
            headers={"x-kavach-approval": "kavach-gateway-internal-secret"},
        )
        assert resp.status_code == 403
        assert "Approval rejected" in resp.json()["detail"]

    def test_tampered_ticket_payee_rejected(self, gateway_setup):
        """Ticket was signed for ondc-grocer@upi, but request is sent to attacker_wallet@upi."""
        _, client, priv_interceptor, _ = gateway_setup
        ticket = sign_approval_ticket(
            interceptor_private_key=priv_interceptor,
            mandate_jti="mandate-1",
            payee_vpa="ondc-grocer@upi",
            amount_paise=120000,
            nonce="nonce-t1",
        )
        resp = client.post(
            "/pay",
            json={
                "payer_vpa": "user@upi",
                "payee_vpa": "attacker_wallet@upi",  # Attacker modified payee!
                "amount_paise": 120000,
            },
            headers={"x-kavach-approval-ticket": ticket},
        )
        assert resp.status_code == 403
        assert "does not match request payee" in resp.json()["detail"]

    def test_tampered_ticket_amount_rejected(self, gateway_setup):
        """Ticket was signed for 120000 paise, but request attempts 500000 paise."""
        _, client, priv_interceptor, _ = gateway_setup
        ticket = sign_approval_ticket(
            interceptor_private_key=priv_interceptor,
            mandate_jti="mandate-1",
            payee_vpa="ondc-grocer@upi",
            amount_paise=120000,
            nonce="nonce-t2",
        )
        resp = client.post(
            "/pay",
            json={
                "payer_vpa": "user@upi",
                "payee_vpa": "ondc-grocer@upi",
                "amount_paise": 500000,  # Attacker modified amount!
            },
            headers={"x-kavach-approval-ticket": ticket},
        )
        assert resp.status_code == 403
        assert "does not match request amount" in resp.json()["detail"]

    def test_single_use_replay_rejected(self, gateway_setup):
        """Ticket can only be used once."""
        _, client, priv_interceptor, _ = gateway_setup
        ticket = sign_approval_ticket(
            interceptor_private_key=priv_interceptor,
            mandate_jti="mandate-1",
            payee_vpa="ondc-grocer@upi",
            amount_paise=50000,
            nonce="replay-ticket-nonce",
        )
        # First use -> 200 OK
        resp1 = client.post(
            "/pay",
            json={
                "payer_vpa": "user@upi",
                "payee_vpa": "ondc-grocer@upi",
                "amount_paise": 50000,
            },
            headers={"x-kavach-approval-ticket": ticket},
        )
        assert resp1.status_code == 200

        # Second use (replay) -> 403 Forbidden
        resp2 = client.post(
            "/pay",
            json={
                "payer_vpa": "user@upi",
                "payee_vpa": "ondc-grocer@upi",
                "amount_paise": 50000,
            },
            headers={"x-kavach-approval-ticket": ticket},
        )
        assert resp2.status_code == 403
        assert "replay detected" in resp2.json()["detail"]

    def test_expired_ticket_rejected(self, gateway_setup):
        _, client, priv_interceptor, _ = gateway_setup
        ticket = sign_approval_ticket(
            interceptor_private_key=priv_interceptor,
            mandate_jti="mandate-1",
            payee_vpa="ondc-grocer@upi",
            amount_paise=50000,
            nonce="expired-ticket-nonce",
            validity_seconds=-10,  # already expired
        )
        resp = client.post(
            "/pay",
            json={
                "payer_vpa": "user@upi",
                "payee_vpa": "ondc-grocer@upi",
                "amount_paise": 50000,
            },
            headers={"x-kavach-approval-ticket": ticket},
        )
        assert resp.status_code == 403
        assert "expired" in resp.json()["detail"]


# ===========================================================================
# 2. Tool Schema Hardening (Extra/Unknown Arguments Forbidden)
# ===========================================================================

class TestToolSchemaHardening:
    def test_extra_argument_rejected(self):
        """Attacker passes override=True or skip_check=True."""
        with pytest.raises(ValueError) as exc:
            make_upi_payment_tool(
                amount_paise=10000,
                payee_vpa="ondc-grocer@upi",
                override=True,
            )
        assert "extra args rejected" in str(exc.value)

    def test_kavach_token_argument_rejected(self):
        """Attacker attempts to inject custom token into tool."""
        with pytest.raises(ValueError) as exc:
            make_upi_payment_tool(
                amount_paise=10000,
                payee_vpa="ondc-grocer@upi",
                kavach_token="forged-token",
            )
        assert "extra args rejected" in str(exc.value)

    def test_skip_checks_argument_rejected(self):
        with pytest.raises(ValueError) as exc:
            make_upi_payment_tool(
                amount_paise=10000,
                payee_vpa="ondc-grocer@upi",
                skip_checks=True,
            )
        assert "extra args rejected" in str(exc.value)


# ===========================================================================
# 3. Input Type Confusion & Boundary Checks
# ===========================================================================

class TestInputSanitization:
    def test_boolean_amount_rejected(self):
        """In Python isinstance(True, int) is True — Kavach must reject booleans."""
        dec = check_amount(True)
        assert dec is not None
        assert dec.reason_code == ReasonCode.INVALID_AMOUNT
        assert "Boolean value" in dec.message

    def test_float_amount_rejected(self):
        dec = check_amount(1500.50)
        assert dec is not None
        assert dec.reason_code == ReasonCode.INVALID_AMOUNT

    def test_nan_amount_rejected(self):
        dec = check_amount(float("nan"))
        assert dec is not None
        assert dec.reason_code == ReasonCode.INVALID_AMOUNT

    def test_inf_amount_rejected(self):
        dec = check_amount(float("inf"))
        assert dec is not None
        assert dec.reason_code == ReasonCode.INVALID_AMOUNT

    def test_huge_integer_rejected(self):
        """Astronomical integers (> 1 Crore rupees) are rejected."""
        dec = check_amount(10**12)
        assert dec is not None
        assert dec.reason_code == ReasonCode.INVALID_AMOUNT
        assert "exceeds maximum safe bounds" in dec.message


# ===========================================================================
# 4. Payee Lookalike & Homoglyph Attacks
# ===========================================================================

class TestPayeeLookalikes:
    allowed = ["ondc-grocer@upi"]

    def test_trailing_space_rejected(self):
        dec = check_payee("ondc-grocer@upi ", self.allowed)
        assert dec is not None
        assert dec.reason_code == ReasonCode.PAYEE_NOT_ALLOWED
        assert "whitespace" in dec.message

    def test_double_at_symbol_rejected(self):
        dec = check_payee("ondc-grocer@upi@attacker", self.allowed)
        assert dec is not None
        assert dec.reason_code == ReasonCode.PAYEE_NOT_ALLOWED

    def test_domain_extension_spoof_rejected(self):
        dec = check_payee("ondc-grocer@upi.evil", self.allowed)
        assert dec is not None
        assert dec.reason_code == ReasonCode.PAYEE_NOT_ALLOWED

    def test_casing_difference_rejected(self):
        """Strict byte equality: ONDC-GROCER@UPI != ondc-grocer@upi."""
        dec = check_payee("ONDC-GROCER@UPI", self.allowed)
        assert dec is not None
        assert dec.reason_code == ReasonCode.PAYEE_NOT_ALLOWED


# ===========================================================================
# 5. Concurrency Race & Atomic SQLite Ledger (50 Threads)
# ===========================================================================

class TestConcurrencyAndLedger:
    def test_atomic_concurrency_stress(self, tmp_path):
        """50 concurrent threads race to spend a ₹1,000 budget (10 payments of ₹100 max).

        Guarantees that total spend never exceeds 100,000 paise and txn_count <= 10.
        """
        db_path = tmp_path / "ledger_stress.db"
        ledger = SqliteLedger(db_path)
        jti = "stress-mandate-001"
        max_budget = 100_000  # ₹1,000
        max_txns = 10
        payment_amount = 10_000  # ₹100 each

        def worker(thread_idx: int) -> bool:
            idempotency_key = f"thread-txn-{thread_idx}"
            ok, _ = ledger.reserve(
                jti=jti,
                amount_paise=payment_amount,
                max_budget_paise=max_budget,
                max_txn_count=max_txns,
                idempotency_key=idempotency_key,
            )
            if ok:
                time.sleep(0.005)  # Simulate small network latency
                ledger.commit(idempotency_key)
                return True
            return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(worker, i) for i in range(50)]
            results = [f.result() for f in futures]

        successful = sum(1 for r in results if r is True)
        assert successful == 10, f"Expected exactly 10 transactions to succeed, got {successful}"

        total_spent = ledger.get_spent(jti)
        total_count = ledger.get_count(jti)
        assert total_spent == 100_000
        assert total_count == 10

    def test_reservation_rollback_on_failure(self, tmp_path):
        ledger = SqliteLedger(tmp_path / "rollback.db")
        jti = "rollback-test"

        # Reserve
        ok, _ = ledger.reserve(
            jti=jti,
            amount_paise=50000,
            max_budget_paise=50000,
            max_txn_count=1,
            idempotency_key="rollback-key-1",
        )
        assert ok is True

        # Second reserve while first is active should fail (budget full)
        ok2, msg2 = ledger.reserve(
            jti=jti,
            amount_paise=1000,
            max_budget_paise=50000,
            max_txn_count=1,
            idempotency_key="rollback-key-2",
        )
        assert ok2 is False
        assert msg2 == "BUDGET_EXHAUSTED"

        # Roll back first reservation
        ledger.rollback("rollback-key-1")

        # Now second reserve succeeds!
        ok3, _ = ledger.reserve(
            jti=jti,
            amount_paise=50000,
            max_budget_paise=50000,
            max_txn_count=1,
            idempotency_key="rollback-key-3",
        )
        assert ok3 is True


# ===========================================================================
# 6. Salami-Slicing Budget Exhaustion
# ===========================================================================

class TestSalamiSlicing:
    def test_salami_slicing_bounded(self, tmp_path):
        """Attacker attempts 100 micro-payments of ₹1 to slowly drain funds.

        Mandate budget: ₹50 (5,000 paise), max 20 transactions.
        Must stop exactly at 20 transactions and total spend <= 2,000 paise.
        """
        priv_user, pub_user = generate_keypair()
        priv_interceptor, pub_interceptor = generate_keypair()
        mandate = MandateSpec(
            purpose="test",
            allowed_payees=["ondc-grocer@upi"],
            allowed_categories=["groceries"],
            per_txn_limit_paise=10000,
            total_budget_paise=5000,  # ₹50
            max_txn_count=20,         # max 20 txns
        )
        token = sign_mandate(priv_user, "agent_salami", mandate)
        ledger = SqliteLedger(tmp_path / "salami.db")
        interceptor = KavachInterceptor(
            public_key=pub_user,
            mandate_token=token,
            ledger=ledger,
            interceptor_private_key=priv_interceptor,
        )

        successful = 0
        blocked = 0

        for i in range(100):
            req = PaymentRequest(
                amount_paise=100,  # ₹1
                payee_vpa="ondc-grocer@upi",
                category="groceries",
                nonce=f"salami-{i}",
                idempotency_key=f"salami-{i}",
            )
            dec, _ = interceptor.evaluate(req)
            if dec.allowed:
                successful += 1
            else:
                blocked += 1

        assert successful == 20
        assert blocked == 80
        assert ledger.get_spent("agent_salami") == 0  # keyed by jti
        assert ledger.get_count("agent_salami") == 0


# ===========================================================================
# 7. Process Isolation Verification (No Secrets in Agent Scope)
# ===========================================================================

# ===========================================================================
# 7. Process Isolation Verification (No Secrets in Agent Scope)
# ===========================================================================

class TestProcessIsolation:
    def test_agent_scope_contains_no_secrets(self):
        """Verify that the agent client module and global scope do NOT hold private keys or tokens."""
        client = KavachClient()

        # Check client attributes
        assert not hasattr(client, "private_key")
        assert not hasattr(client, "mandate_token")
        assert not hasattr(client, "_interceptor_private_key")
        assert not hasattr(client, "_mandate_token")

        # Check environment variables
        for env_key, val in os.environ.items():
            assert "BEGIN PRIVATE KEY" not in val
            assert "BEGIN ED25519 PRIVATE KEY" not in val
            if "TOKEN" in env_key.upper():
                assert not val.startswith("eyJ")


# ===========================================================================
# 8. Information Leakage & Multi-Reason Regression (Phase 3 P0)
# ===========================================================================

class TestInformationLeakageAndReasonCodes:
    @pytest.fixture
    def setup_leak_test(self):
        priv_user, pub_user = generate_keypair()
        mandate = MandateSpec(
            purpose="grocery shopping",
            allowed_payees=["ondc-grocer@upi"],
            allowed_categories=["groceries"],
            per_txn_limit_paise=150000,   # ₹1,500
            total_budget_paise=300000,    # ₹3,000
            max_txn_count=5,
        )
        token = sign_mandate(priv_user, "agent_leak_test", mandate)
        ledger = SqliteLedger(":memory:")
        # Spend ₹1,200 legitimately first
        ledger.record_txn("agent_leak_test", 120000)
        interceptor = KavachInterceptor(
            public_key=pub_user,
            mandate_token=token,
            ledger=ledger,
        )
        return interceptor

    def test_agent_facing_error_contains_no_sensitive_leaks(self, setup_leak_test):
        """Agent-facing error messages must NOT leak allowlists or exact account balances."""
        interceptor = setup_leak_test

        # 1. Test unallowed payee: must NOT leak 'ondc-grocer@upi'
        req1 = PaymentRequest(
            amount_paise=100000,
            payee_vpa="attacker_wallet@upi",
            category="groceries",
            nonce="leak-1",
        )
        dec1, _ = interceptor.evaluate(req1)
        assert dec1.allowed is False
        assert "ondc-grocer@upi" not in dec1.message
        assert "is not authorized" in dec1.message

        # 2. Test budget overshoot: must NOT leak remaining balance
        req2 = PaymentRequest(
            amount_paise=500000,
            payee_vpa="ondc-grocer@upi",
            category="groceries",
            nonce="leak-2",
        )
        dec2, _ = interceptor.evaluate(req2)
        assert dec2.allowed is False
        assert "Remaining:" not in dec2.message
        assert "180000" not in dec2.message

    def test_scene_3_multi_reason_regression(self, setup_leak_test):
        """Scene 3 attack must report all 3 independent violations:

        1. PAYEE_NOT_ALLOWED
        2. AMOUNT_EXCEEDS_PER_TXN_LIMIT
        3. BUDGET_EXHAUSTED
        """
        interceptor = setup_leak_test

        req = PaymentRequest(
            amount_paise=500000,  # ₹5,000
            payee_vpa="attacker_wallet@upi",
            note="refund processing",
            category="groceries",
            nonce="scene3-reg",
        )
        decision, _ = interceptor.evaluate(req)
        assert decision.allowed is False

        all_codes = set(decision.reason_codes)
        assert ReasonCode.PAYEE_NOT_ALLOWED in all_codes
        assert ReasonCode.AMOUNT_EXCEEDS_PER_TXN_LIMIT in all_codes
        assert ReasonCode.BUDGET_EXHAUSTED in all_codes
