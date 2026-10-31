"""Hypothesis Property-Based Tests for Kavach Security Invariants (I1 - I6).

Formally tests the guarantees defined in docs/INVARIANTS.md:
- I1: Cumulative spend <= total_budget_paise under any arbitrary payment sequence.
- I2: Strict payee containment (no lookalike, case variance, or arbitrary string escapes).
- I3: Transaction count ceiling (count <= max_txn_count).
- I4: Temporal validity (nbf/exp) and non-revocation.
- I5: Ticket & audit bijectivity (1 gateway settle == 1 ticket == 1 audit entry).
- I6: Fail-closed availability & mid-flight crash resilience.
"""

from __future__ import annotations

import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import hypothesis.strategies as st
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings

from kavach.audit import AuditLog
from kavach.client import KavachClient
from kavach.interceptor import KavachInterceptor
from kavach.issuer import generate_keypair, sign_approval_ticket, sign_mandate
from kavach.ledger_db import SqliteLedger
from kavach.mock_upi import MockUPIGateway, create_app
from kavach.models import (
    Decision,
    MandateSpec,
    PaymentRequest,
    ReasonCode,
)


@pytest.fixture(scope="module")
def user_keys():
    return generate_keypair()


@pytest.fixture(scope="module")
def interceptor_keys():
    return generate_keypair()


def make_interceptor(
    user_keys,
    interceptor_keys,
    db_path: Path | None = None,
    total_budget_paise: int = 100_000,
    per_txn_limit_paise: int = 25_000,
    max_txn_count: int = 5,
    allowed_payees: list[str] | None = None,
    validity_seconds: int = 3600,
    nbf_offset_seconds: int = 0,
    revoked_jtis: frozenset[str] | None = None,
) -> tuple[KavachInterceptor, SqliteLedger, str, AuditLog]:
    user_priv, user_pub = user_keys
    interceptor_priv, _ = interceptor_keys

    spec = MandateSpec(
        purpose="Property testing mandate",
        allowed_payees=allowed_payees or ["grocer@upi", "merchant@upi"],
        allowed_categories=["groceries", "retail"],
        per_txn_limit_paise=per_txn_limit_paise,
        total_budget_paise=total_budget_paise,
        max_txn_count=max_txn_count,
    )
    token = sign_mandate(
        private_key=user_priv,
        agent_id="agent-hypothesis",
        mandate=spec,
        validity_seconds=validity_seconds,
        nbf_offset=nbf_offset_seconds,
    )
    ledger = SqliteLedger(db_path)
    audit_file = (db_path.parent / f"audit_{uuid.uuid4().hex[:8]}.jsonl") if db_path else None
    audit_log = AuditLog(path=audit_file)

    interceptor = KavachInterceptor(
        public_key=user_pub,
        mandate_token=token,
        ledger=ledger,
        audit_log=audit_log,
        revoked_jtis=revoked_jtis or frozenset(),
        interceptor_private_key=interceptor_priv,
    )
    return interceptor, ledger, token, audit_log


# ==============================================================================
# Invariant 1: Total Budget Invariance
# sum(approved_paise) <= total_budget_paise
# ==============================================================================

class TestInvariant1Budget:
    """Hypothesis tests for Invariant 1: Total Budget Invariance."""

    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(amounts=st.lists(st.integers(min_value=-500, max_value=120_000), max_size=20))
    def test_i1_cumulative_spend_never_exceeds_total_budget(self, user_keys, interceptor_keys, amounts):
        total_budget = 50_000  # ₹500
        interceptor, ledger, token, _ = make_interceptor(
            user_keys,
            interceptor_keys,
            None,
            total_budget_paise=total_budget,
            per_txn_limit_paise=20_000,
            max_txn_count=20,
        )

        import jwt
        claims = jwt.decode(token, options={"verify_signature": False})
        jti = claims["jti"]

        approved_total = 0
        for amt in amounts:
            req = PaymentRequest(
                amount_paise=amt,
                payee_vpa="grocer@upi",
                category="groceries",
                nonce=str(uuid.uuid4()),
            )
            decision, _ = interceptor.evaluate(req)
            if decision.allowed:
                assert amt > 0
                assert amt <= 20_000
                approved_total += amt

        # Mathematical Invariant 1 check
        assert approved_total <= total_budget, f"Approved {approved_total} exceeded budget {total_budget}"
        spent_in_ledger = ledger.get_spent(jti)
        assert spent_in_ledger == approved_total
        assert spent_in_ledger <= total_budget


# ==============================================================================
# Invariant 2: Strict Payee Containment
# Every approved payee in allowed_payees
# ==============================================================================

class TestInvariant2PayeeContainment:
    """Hypothesis tests for Invariant 2: Strict Payee Containment."""

    @settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(candidate_payee=st.text(min_size=1, max_size=60))
    def test_i2_no_unauthorized_payee_can_ever_be_approved(self, user_keys, interceptor_keys, candidate_payee):
        allowed = ["grocer@upi", "merchant@upi"]
        interceptor, _, _, _ = make_interceptor(
            user_keys,
            interceptor_keys,
            None,
            allowed_payees=allowed,
            total_budget_paise=100_000,
        )

        req = PaymentRequest(
            amount_paise=1000,
            payee_vpa=candidate_payee,
            category="groceries",
            nonce=str(uuid.uuid4()),
        )
        decision, _ = interceptor.evaluate(req)

        if decision.allowed:
            # Mathematical Invariant 2 check
            assert candidate_payee in allowed
        else:
            if candidate_payee not in allowed:
                assert ReasonCode.PAYEE_NOT_ALLOWED in decision.reason_codes


# ==============================================================================
# Invariant 3: Transaction Count Ceiling
# count(approved) <= max_txn_count
# ==============================================================================

class TestInvariant3TxnCountCeiling:
    """Hypothesis tests for Invariant 3: Transaction Count Ceiling."""

    @settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        max_count=st.integers(min_value=1, max_value=5),
        payment_attempts=st.integers(min_value=1, max_value=12),
    )
    def test_i3_approved_count_never_exceeds_max_txn_count(
        self, user_keys, interceptor_keys, max_count, payment_attempts
    ):
        interceptor, ledger, token, _ = make_interceptor(
            user_keys,
            interceptor_keys,
            None,
            total_budget_paise=1_000_000,
            per_txn_limit_paise=10_000,
            max_txn_count=max_count,
        )

        import jwt
        claims = jwt.decode(token, options={"verify_signature": False})
        jti = claims["jti"]

        approved_count = 0
        for i in range(payment_attempts):
            req = PaymentRequest(
                amount_paise=100,
                payee_vpa="grocer@upi",
                category="groceries",
                nonce=f"nonce-i3-{i}-{uuid.uuid4()}",
            )
            decision, _ = interceptor.evaluate(req)
            if decision.allowed:
                approved_count += 1
            else:
                if approved_count >= max_count:
                    assert ReasonCode.TXN_COUNT_EXCEEDED in decision.reason_codes

        # Mathematical Invariant 3 check
        assert approved_count <= max_count
        assert ledger.get_count(jti) == approved_count


# ==============================================================================
# Invariant 4: Temporal Validity & Non-Revocation
# nbf <= t < exp and jti not in revoked
# ==============================================================================

class TestInvariant4TemporalValidity:
    """Hypothesis tests for Invariant 4: Temporal Validity and Non-Revocation."""

    @settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(offset_seconds=st.integers(min_value=-600, max_value=1200))
    def test_i4_temporal_validity_boundaries(
        self, user_keys, interceptor_keys, offset_seconds
    ):
        now = int(time.time())
        simulated_now = now + offset_seconds

        # Mandate valid from now+100 to now+400
        interceptor, _, _, _ = make_interceptor(
            user_keys,
            interceptor_keys,
            None,
            validity_seconds=300,
            nbf_offset_seconds=100,
        )

        req = PaymentRequest(
            amount_paise=1000,
            payee_vpa="grocer@upi",
            category="groceries",
            nonce=str(uuid.uuid4()),
        )

        with patch("time.time", return_value=simulated_now):
            decision, _ = interceptor.evaluate(req)

        # Boundaries: valid iff now + 100 <= simulated_now < now + 300
        if simulated_now < now + 100:
            assert not decision.allowed
            assert ReasonCode.MANDATE_NOT_YET_VALID in decision.reason_codes
        elif simulated_now >= now + 300:
            assert not decision.allowed
            assert ReasonCode.MANDATE_EXPIRED in decision.reason_codes
        else:
            assert decision.allowed

    def test_i4_revocation_immediately_blocks_all_spend(self, user_keys, interceptor_keys):
        interceptor, _, token, _ = make_interceptor(
            user_keys,
            interceptor_keys,
            None,
        )

        import jwt
        claims = jwt.decode(token, options={"verify_signature": False})
        jti = claims["jti"]

        # 1. Before revocation: payment succeeds
        req1 = PaymentRequest(
            amount_paise=1000,
            payee_vpa="grocer@upi",
            category="groceries",
            nonce="req1",
        )
        dec1, _ = interceptor.evaluate(req1)
        assert dec1.allowed is True

        # 2. Revoke mandate
        interceptor.update_revoked_jtis(frozenset([jti]))

        # 3. After revocation: payment fails immediately
        req2 = PaymentRequest(
            amount_paise=1000,
            payee_vpa="grocer@upi",
            category="groceries",
            nonce="req2",
        )
        dec2, _ = interceptor.evaluate(req2)
        assert dec2.allowed is False
        assert ReasonCode.MANDATE_REVOKED in dec2.reason_codes


# ==============================================================================
# Invariant 5: Ticket & Audit Bijectivity
# 1 Gateway Settle == 1 ApprovalTicket == 1 AuditEntry
# ==============================================================================

class TestInvariant5Bijectivity:
    """Hypothesis tests for Invariant 5: Ticket and Audit Bijectivity."""

    def test_i5_exact_bijective_mapping_and_replay_rejection(
        self, user_keys, interceptor_keys
    ):
        interceptor, _, token, audit_log = make_interceptor(
            user_keys,
            interceptor_keys,
            None,
            total_budget_paise=100_000,
            per_txn_limit_paise=50_000,
            max_txn_count=5,
        )

        _, interceptor_pub = interceptor_keys
        gateway = MockUPIGateway(interceptor_public_key=interceptor_pub)
        gateway.set_balance("user@upi", 100_000)
        app = create_app(gateway=gateway)
        client = TestClient(app)

        # Settle 3 distinct payments
        for i in range(3):
            req = PaymentRequest(
                amount_paise=2500,
                payee_vpa="grocer@upi",
                category="groceries",
                nonce=f"nonce-i5-{i}",
            )
            decision, _ = interceptor.evaluate(req)
            assert decision.allowed is True
            assert decision.approval_ticket is not None

            # Gateway settlement
            resp = client.post(
                "/pay",
                json={
                    "amount_paise": 2500,
                    "payee_vpa": "grocer@upi",
                    "payer_vpa": "user@upi",
                },
                headers={"x-kavach-approval-ticket": decision.approval_ticket},
            )
            assert resp.status_code == 200
            assert resp.json()["success"] is True

            # Replay of the same ticket must be rejected (single-use)
            replay_resp = client.post(
                "/pay",
                json={
                    "amount_paise": 2500,
                    "payee_vpa": "grocer@upi",
                    "payer_vpa": "user@upi",
                },
                headers={"x-kavach-approval-ticket": decision.approval_ticket},
            )
            assert replay_resp.status_code == 403
            assert "already been used" in replay_resp.json()["detail"] or "replay" in replay_resp.json()["detail"].lower()

        # Bijectivity Check:
        # 3 Gateway settlements == 3 audit log entries == 3 ledger entries
        assert len(audit_log.entries) == 3
        assert audit_log.verify_chain() is True


# ==============================================================================
# Invariant 6: Fail-Closed Availability & Mid-Flight Crash Resilience
# ==============================================================================

class TestInvariant6CrashResilience:
    """Hypothesis tests for Invariant 6: Fail-Closed and Crash Resilience."""

    def test_i6_midflight_crash_rolls_back_reservation(self, user_keys, interceptor_keys):
        """Simulate an unhandled crash inside the interceptor after reserve() but before commit()."""
        interceptor, ledger, token, _ = make_interceptor(
            user_keys,
            interceptor_keys,
            None,
            total_budget_paise=10_000,
            per_txn_limit_paise=10_000,
            max_txn_count=1,
        )

        import jwt
        claims = jwt.decode(token, options={"verify_signature": False})
        jti = claims["jti"]

        # Patch sign_approval_ticket to raise an unexpected crash simulating power cut / process termination
        with patch("kavach.interceptor.sign_approval_ticket", side_effect=RuntimeError("MID_FLIGHT_CRASH")):
            req = PaymentRequest(
                amount_paise=10_000,
                payee_vpa="grocer@upi",
                category="groceries",
                nonce="crash-nonce",
            )
            decision, _ = interceptor.evaluate(req)

        # Assert Fail-Closed
        assert decision.allowed is False
        assert decision.reason_code == ReasonCode.INTERNAL_ERROR
        assert "fail-closed" in decision.message

        # Assert reservation was rolled back cleanly:
        # Spent must still be 0, count must still be 0!
        assert ledger.get_spent(jti) == 0
        assert ledger.get_count(jti) == 0

        # Subsequent valid transaction can still proceed safely without double-spend or corruption
        req_valid = PaymentRequest(
            amount_paise=10_000,
            payee_vpa="grocer@upi",
            category="groceries",
            nonce="retry-nonce",
        )
        dec_retry, _ = interceptor.evaluate(req_valid)
        assert dec_retry.allowed is True
        assert ledger.get_spent(jti) == 10_000
        assert ledger.get_count(jti) == 1

    def test_i6_client_fails_closed_when_interceptor_unreachable(self):
        """Verify client stub fails closed with zero balance moved if interceptor is down."""
        # Port 59999 has no listener
        client = KavachClient(interceptor_url="http://127.0.0.1:59999")
        result = client.request_payment(
            amount_paise=10_000,
            payee_vpa="grocer@upi",
            note="Payment when interceptor is down",
        )
        assert result.success is False
        assert result.txn_id is None
        assert "interceptor" in result.message.lower() or "connection" in result.message.lower()
