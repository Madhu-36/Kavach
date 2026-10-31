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

import os
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import hypothesis.strategies as st
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings

settings.register_profile("ci", max_examples=1000, deadline=None)
settings.register_profile("dev", max_examples=50)
if "HYPOTHESIS_PROFILE" in os.environ:
    settings.load_profile(os.environ["HYPOTHESIS_PROFILE"])


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

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
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

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
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

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        max_count=st.integers(min_value=1, max_value=50),
        payment_attempts=st.integers(min_value=1, max_value=100),
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

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
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

    def test_i6_real_subprocess_crash_zero_leaked_budget(self, tmp_path):
        """Spawn real subprocess, kill it with TerminateProcess/SIGKILL during reservation, verify recovery."""
        import subprocess
        import sys

        db_path = tmp_path / "crash_test.db"
        jti = "mandate-crash-test"

        worker_code = f"""
import sys, time
from kavach.ledger_db import SqliteLedger

ledger = SqliteLedger(r"{db_path}")
ok, msg = ledger.reserve(
    jti="{jti}",
    amount_paise=50000,
    max_budget_paise=100000,
    max_txn_count=5,
    idempotency_key="crash-idemp-1"
)
sys.stdout.write("RESERVED\\n")
sys.stdout.flush()
time.sleep(30)
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", worker_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            line = proc.stdout.readline().strip()
            assert line == "RESERVED"

            # Forcefully kill process with TerminateProcess / SIGKILL
            proc.kill()
            proc.wait()

            # Restart / re-open ledger in parent process
            recovered_ledger = SqliteLedger(db_path)

            # Assert zero committed spend leaked
            assert recovered_ledger.get_spent(jti) == 0
            assert recovered_ledger.get_count(jti) == 0

            # Recover from the abandoned reservation
            recovered_count = recovered_ledger.recover_from_crash()
            assert recovered_count == 1

            # Assert full original budget is immediately available for next valid payment
            ok_next, msg_next = recovered_ledger.reserve(
                jti=jti,
                amount_paise=100000,
                max_budget_paise=100000,
                max_txn_count=5,
                idempotency_key="clean-idemp-2",
            )
            assert ok_next is True
            assert msg_next == "RESERVED"
            assert recovered_ledger.commit("clean-idemp-2") is True
            assert recovered_ledger.get_spent(jti) == 100000
            assert recovered_ledger.get_count(jti) == 1
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_i6_real_interceptor_service_process_crash_recovery(self, tmp_path):
        """Kill the real Interceptor HTTP service process between reserve and commit, restart, verify zero double-spend."""
        import socket
        import subprocess
        import sys
        import threading
        import time
        import httpx
        import jwt

        from kavach.issuer import (
            generate_keypair,
            load_private_key,
            load_public_key,
            save_keypair,
            sign_mandate,
        )
        from kavach.ledger_db import SqliteLedger
        from kavach.models import MandateSpec

        db_path = tmp_path / "service_crash_test.db"

        # 1. Generate keys and mandate
        user_priv, user_pub = generate_keypair()
        interceptor_priv, interceptor_pub = generate_keypair()
        _, user_pub_path = save_keypair(user_priv, user_pub, tmp_path, prefix="user")
        interceptor_priv_path, _ = save_keypair(interceptor_priv, interceptor_pub, tmp_path, prefix="interceptor")

        spec = MandateSpec(
            purpose="Service Process Crash Test",
            allowed_payees=["ondc-grocer@upi"],
            allowed_categories=["groceries"],
            per_txn_limit_paise=100_000,
            total_budget_paise=100_000,
            max_txn_count=5,
        )
        token = sign_mandate(user_priv, "crash_agent", spec, validity_seconds=3600)
        jti = jwt.decode(token, options={"verify_signature": False})["jti"]

        # Helper to get a free TCP port
        def get_free_port():
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            p = s.getsockname()[1]
            s.close()
            return p

        port1 = get_free_port()

        # 2. Worker code that runs the real FastAPI interceptor service and halts between reserve and commit
        worker_code_crash = f'''
import sys, time, uvicorn
from pathlib import Path
from kavach.service import InterceptorServer, create_service_app
from kavach.issuer import load_public_key, load_private_key

user_pub = load_public_key(Path(r"{user_pub_path}"))
interceptor_priv = load_private_key(Path(r"{interceptor_priv_path}"))

srv = InterceptorServer(
    public_key=user_pub,
    mandate_token="{token}",
    interceptor_private_key=interceptor_priv,
    db_path=r"{db_path}",
)

orig_reserve = srv.ledger.reserve
def hooked_reserve(*args, **kwargs):
    res = orig_reserve(*args, **kwargs)
    sys.stdout.write("RESERVED_BEFORE_COMMIT\\n")
    sys.stdout.flush()
    time.sleep(30)
    return res

srv.ledger.reserve = hooked_reserve
app = create_service_app(srv)
uvicorn.run(app, host="127.0.0.1", port={port1}, log_level="warning")
'''

        proc1 = subprocess.Popen(
            [sys.executable, "-u", "-c", worker_code_crash],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            # Wait for service 1 to become healthy
            client = httpx.Client(timeout=5.0)
            for _ in range(50):
                try:
                    r = client.get(f"http://127.0.0.1:{port1}/health")
                    if r.status_code == 200:
                        break
                except Exception:
                    time.sleep(0.1)

            # Fire payment request in background thread
            def send_req():
                try:
                    client.post(
                        f"http://127.0.0.1:{port1}/evaluate",
                        json={
                            "amount_paise": 50000,
                            "payee_vpa": "ondc-grocer@upi",
                            "category": "groceries",
                            "nonce": "crash-req-1",
                        },
                    )
                except Exception:
                    pass

            t = threading.Thread(target=send_req, daemon=True)
            t.start()

            # Wait for real service to signal reservation completed before commit
            line = proc1.stdout.readline().strip()
            assert line == "RESERVED_BEFORE_COMMIT"

            # Kill the real interceptor service process with TerminateProcess
            proc1.kill()
            proc1.wait()
            time.sleep(0.5)

            # Verify in persistent ledger that spent is 0 and count is 0
            ledger = SqliteLedger(db_path)
            assert ledger.get_spent(jti) == 0
            assert ledger.get_count(jti) == 0

            # Recover uncommitted in-flight reservation
            recovered = ledger.recover_from_crash()
            assert recovered == 1

            # 3. Restart real interceptor service on persistent ledger
            port2 = get_free_port()
            worker_code_clean = f'''
import sys, time, uvicorn
from pathlib import Path
from kavach.service import InterceptorServer, create_service_app
from kavach.issuer import load_public_key, load_private_key

user_pub = load_public_key(Path(r"{user_pub_path}"))
interceptor_priv = load_private_key(Path(r"{interceptor_priv_path}"))

srv = InterceptorServer(
    public_key=user_pub,
    mandate_token="{token}",
    interceptor_private_key=interceptor_priv,
    db_path=r"{db_path}",
)
app = create_service_app(srv)
uvicorn.run(app, host="127.0.0.1", port={port2}, log_level="warning")
'''
            proc2 = subprocess.Popen(
                [sys.executable, "-u", "-c", worker_code_clean],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            try:
                for _ in range(50):
                    try:
                        r = client.get(f"http://127.0.0.1:{port2}/health")
                        if r.status_code == 200:
                            break
                    except Exception:
                        time.sleep(0.1)

                # Execute full budget payment (100,000 paise) against restarted service
                resp = client.post(
                    f"http://127.0.0.1:{port2}/evaluate",
                    json={
                        "amount_paise": 100000,
                        "payee_vpa": "ondc-grocer@upi",
                        "category": "groceries",
                        "nonce": "clean-req-2",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["allowed"] is True
                assert data["reason_code"] == "APPROVED"
                assert data.get("approval_ticket") is not None

                # Verify committed ledger state: exactly 100,000 paise spent, count is 1
                assert ledger.get_spent(jti) == 100000
                assert ledger.get_count(jti) == 1

                # Verify that any further spend is rejected
                resp_over = client.post(
                    f"http://127.0.0.1:{port2}/evaluate",
                    json={
                        "amount_paise": 1000,
                        "payee_vpa": "ondc-grocer@upi",
                        "category": "groceries",
                        "nonce": "clean-req-3",
                    },
                )
                assert resp_over.status_code == 200
                data_over = resp_over.json()
                assert data_over["allowed"] is False
                assert "BUDGET_EXHAUSTED" in data_over["reason_codes"]

            finally:
                if proc2.poll() is None:
                    proc2.kill()
                    proc2.wait()

        finally:
            if proc1.poll() is None:
                proc1.kill()
                proc1.wait()

    def test_i6_crash_after_settlement_reconciles_and_prevents_double_spend(self, tmp_path):
        """Kill interceptor service after gateway settles but before ledger commit.
        
        Verify recovery reconciles against gateway:
        - Commits the settled payment (does NOT roll it back).
        - Prevents double-spend on retry.
        - Ensures zero leaked reservations.
        - Verifies ledger and gateway balance/spent strictly agree.
        """
        import os
        import socket
        import subprocess
        import sys
        import threading
        import time
        import httpx
        import jwt

        from kavach.issuer import (
            generate_keypair,
            load_private_key,
            load_public_key,
            save_keypair,
            sign_mandate,
        )
        from kavach.ledger_db import SqliteLedger
        from kavach.mock_upi import MockUPIGateway, create_app as create_gateway_app
        from kavach.models import MandateSpec

        db_path = tmp_path / "settlement_crash.db"

        # 1. Setup Keys and Mandate (₹1,000 budget, max 5 txns)
        user_priv, user_pub = generate_keypair()
        interceptor_priv, interceptor_pub = generate_keypair()
        _, user_pub_path = save_keypair(user_priv, user_pub, tmp_path, prefix="user_sc")
        interceptor_priv_path, _ = save_keypair(interceptor_priv, interceptor_pub, tmp_path, prefix="interceptor_sc")

        spec = MandateSpec(
            purpose="Settlement Crash Reconciliation Test",
            allowed_payees=["ondc-grocer@upi"],
            allowed_categories=["groceries"],
            per_txn_limit_paise=100_000,
            total_budget_paise=100_000,
            max_txn_count=5,
        )
        token = sign_mandate(user_priv, "agent_crash_settle", spec, validity_seconds=3600)
        jti = jwt.decode(token, options={"verify_signature": False})["jti"]

        def get_free_port():
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            p = s.getsockname()[1]
            s.close()
            return p

        port_interceptor = get_free_port()

        # 2. Setup Mock UPI Gateway on background thread
        gateway = MockUPIGateway(interceptor_public_key=interceptor_pub)
        gateway.set_balance("user@upi", 100_000)      # ₹1,000
        gateway.set_balance("ondc-grocer@upi", 0)

        from fastapi.testclient import TestClient
        gw_client = TestClient(create_gateway_app(gw=gateway))

        # 3. Launch Interceptor Service Subprocess with Test Hook Activated
        worker_code = f'''
import os, sys, uvicorn
from pathlib import Path
from kavach.service import InterceptorServer, create_service_app
from kavach.issuer import load_public_key, load_private_key

user_pub = load_public_key(Path(r"{user_pub_path}"))
interceptor_priv = load_private_key(Path(r"{interceptor_priv_path}"))

srv = InterceptorServer(
    public_key=user_pub,
    mandate_token="{token}",
    interceptor_private_key=interceptor_priv,
    db_path=r"{db_path}",
)
app = create_service_app(srv)
uvicorn.run(app, host="127.0.0.1", port={port_interceptor}, log_level="warning")
'''
        env = os.environ.copy()
        env["KAVACH_TEST_HOOK_CRASH_AFTER_SETTLEMENT"] = "1"
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", worker_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        try:
            http_client = httpx.Client(timeout=5.0)
            for _ in range(50):
                try:
                    r = http_client.get(f"http://127.0.0.1:{port_interceptor}/health")
                    if r.status_code == 200:
                        break
                except Exception:
                    time.sleep(0.1)

            # Step A: Interceptor evaluates and reserves 50,000 paise (auto_commit=False)
            eval_resp = http_client.post(
                f"http://127.0.0.1:{port_interceptor}/evaluate?auto_commit=false",
                json={
                    "amount_paise": 50000,
                    "payee_vpa": "ondc-grocer@upi",
                    "category": "groceries",
                    "nonce": "settle-nonce-1",
                    "idempotency_key": "settle-idemp-1",
                },
            )
            assert eval_resp.status_code == 200
            eval_data = eval_resp.json()
            assert eval_data["allowed"] is True
            approval_ticket = eval_data["approval_ticket"]
            assert approval_ticket is not None

            # Step B: Gateway settles the payment
            pay_resp = gw_client.post(
                "/pay",
                json={
                    "payer_vpa": "user@upi",
                    "payee_vpa": "ondc-grocer@upi",
                    "amount_paise": 50000,
                    "txn_id": "txn-settle-1",
                },
                headers={"x-kavach-approval-ticket": approval_ticket},
            )
            assert pay_resp.status_code == 200
            assert pay_resp.json()["success"] is True
            assert gateway.get_balance("user@upi") == 50000
            assert gateway.get_balance("ondc-grocer@upi") == 50000

            # Step C: Client calls /commit on Interceptor; Test Hook triggers crash halt!
            def call_commit():
                try:
                    http_client.post(
                        f"http://127.0.0.1:{port_interceptor}/commit",
                        json={"idempotency_key": "settle-idemp-1"},
                    )
                except Exception:
                    pass

            t_commit = threading.Thread(target=call_commit, daemon=True)
            t_commit.start()

            # Wait for test hook to flush before commit
            line = proc.stdout.readline().strip()
            assert line == "CRASH_AFTER_SETTLEMENT_BEFORE_COMMIT"

            # KILL the real interceptor service process with TerminateProcess
            proc.kill()
            proc.wait()
            time.sleep(0.5)

            # Step D: Verify crash state in persistent ledger
            recovered_ledger = SqliteLedger(db_path)
            # Before recovery, committed spent is still 0 (reservation was uncommitted)
            assert recovered_ledger.get_spent(jti) == 0

            # Step E: Recovery with gateway reconciliation (The Bug Fix!)
            # If naive recovery ran, it would mark it ROLLED_BACK (leaking money and enabling double-spend).
            # With gateway reconciliation, it discovers the gateway already settled the payment!
            recovered_count = recovered_ledger.recover_from_crash(
                gateway_reconciler=gateway.is_ticket_settled
            )
            assert recovered_count == 1

            # Step F: Assert Ledger & Gateway Agreement & No Double Spend
            # 1. Budget reflects the settled payment
            assert recovered_ledger.get_spent(jti) == 50000
            assert recovered_ledger.get_count(jti) == 1

            # 2. Gateway and Ledger agree on amounts
            assert gateway.get_balance("user@upi") == 50000
            assert recovered_ledger.get_spent(jti) == 50000

            # 3. No double-spend on retry (idempotency key returns ALREADY_COMMITTED)
            ok_retry, msg_retry = recovered_ledger.reserve(
                jti=jti,
                amount_paise=50000,
                max_budget_paise=100000,
                max_txn_count=5,
                idempotency_key="settle-idemp-1",
            )
            assert ok_retry is True
            assert msg_retry == "ALREADY_COMMITTED"
            # Committed spend has NOT doubled!
            assert recovered_ledger.get_spent(jti) == 50000

            # 4. Subsequent payments can spend remaining budget (₹500), but not more
            ok_next, _ = recovered_ledger.reserve(
                jti=jti,
                amount_paise=50000,
                max_budget_paise=100000,
                max_txn_count=5,
                idempotency_key="clean-next-2",
            )
            assert ok_next is True
            assert recovered_ledger.commit("clean-next-2") is True
            assert recovered_ledger.get_spent(jti) == 100000

            # Further spend is blocked
            ok_over, msg_over = recovered_ledger.reserve(
                jti=jti,
                amount_paise=1000,
                max_budget_paise=100000,
                max_txn_count=5,
                idempotency_key="blocked-over-3",
            )
            assert ok_over is False
            assert msg_over == "BUDGET_EXHAUSTED"

        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def test_i6_crash_between_ticket_generation_and_gateway_settlement(user_keys, interceptor_keys):
    """Confirm crash between ticket generation and gateway settlement rolls back cleanly on restart."""
    user_priv, user_pub = user_keys
    interceptor_priv, interceptor_pub = interceptor_keys

    spec = MandateSpec(
        purpose="groceries",
        allowed_payees=["ondc-grocer@upi"],
        allowed_categories=["groceries"],
        per_txn_limit_paise=50_000,
        total_budget_paise=100_000,
        max_txn_count=5,
    )
    token = sign_mandate(user_priv, "agent_1", spec)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = str(Path(tmpdir) / "kavach_ledger.db")
        gateway = MockUPIGateway(interceptor_public_key=interceptor_pub)
        gateway.set_balance("user@upi", 100_000)
        gateway.set_balance("ondc-grocer@upi", 0)

        # 1. First run: interceptor evaluates and issues ticket, persisting reservation
        ledger = SqliteLedger(db_path)
        audit = AuditLog()
        interceptor = KavachInterceptor(
            public_key=user_pub,
            mandate_token=token,
            ledger=ledger,
            audit_log=audit,
            interceptor_private_key=interceptor_priv,
        )

        req = PaymentRequest(
            amount_paise=40_000,
            payee_vpa="ondc-grocer@upi",
            category="groceries",
            note="crash test before gateway",
            idempotency_key="crash-pre-gw-1",
        )
        decision, claims = interceptor.evaluate(req, auto_commit=False)
        assert decision.allowed is True
        assert decision.approval_ticket is not None
        jti = claims.jti

        # Simulate CRASH before gateway call: no call to gateway /pay was made!
        del interceptor
        del ledger
        import gc
        gc.collect()

        # 2. Restart and recover with gateway reconciliation
        restarted_ledger = SqliteLedger(db_path)
        assert restarted_ledger.get_spent(jti) == 0

        # Gateway reports ticket was never settled
        recovered = restarted_ledger.recover_from_crash(
            gateway_reconciler=gateway.is_ticket_settled
        )
        assert recovered == 1

        # Assert uncommitted reservation rolled back: spent is 0, count is 0
        assert restarted_ledger.get_spent(jti) == 0
        assert restarted_ledger.get_count(jti) == 0
        assert gateway.get_balance("user@upi") == 100_000
        assert gateway.get_balance("ondc-grocer@upi") == 0
        del restarted_ledger
        gc.collect()


def test_i6_gateway_offline_during_restart_fails_closed(user_keys, interceptor_keys):
    """Confirm recovery when gateway is offline during restart fails closed (preserves reservation)."""
    user_priv, user_pub = user_keys
    interceptor_priv, interceptor_pub = interceptor_keys

    spec = MandateSpec(
        purpose="groceries",
        allowed_payees=["ondc-grocer@upi"],
        allowed_categories=["groceries"],
        per_txn_limit_paise=60_000,
        total_budget_paise=100_000,
        max_txn_count=5,
    )
    token = sign_mandate(user_priv, "agent_1", spec)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = str(Path(tmpdir) / "kavach_ledger.db")

        # 1. Make a reservation
        ledger = SqliteLedger(db_path)
        audit = AuditLog()
        interceptor = KavachInterceptor(
            public_key=user_pub,
            mandate_token=token,
            ledger=ledger,
            audit_log=audit,
            interceptor_private_key=interceptor_priv,
        )

        req = PaymentRequest(
            amount_paise=60_000,
            payee_vpa="ondc-grocer@upi",
            category="groceries",
            note="offline test",
            idempotency_key="offline-res-1",
        )
        decision, claims = interceptor.evaluate(req, auto_commit=False)
        assert decision.allowed is True
        jti = claims.jti

        del interceptor
        del ledger
        import gc
        gc.collect()

        # 2. Restart while gateway is OFFLINE (reconciler raises ConnectionError)
        restarted_ledger = SqliteLedger(db_path)

        def offline_gateway_reconciler(ticket_id: str):
            raise ConnectionError("Gateway service unreachable: 503 Service Unavailable")

        # Recovery should fail closed: 0 recovered, reservation stays active
        recovered = restarted_ledger.recover_from_crash(
            gateway_reconciler=offline_gateway_reconciler
        )
        assert recovered == 0

        # Budget remains reserved: attempting to spend 50,000 (which exceeds remaining 40,000) is BLOCKED
        ok_extra, msg_extra = restarted_ledger.reserve(
            jti=jti,
            amount_paise=50_000,
            max_budget_paise=100_000,
            max_txn_count=5,
            idempotency_key="extra-spend-2",
        )
        assert ok_extra is False
        assert msg_extra == "BUDGET_EXHAUSTED"
        del restarted_ledger
        gc.collect()


def test_ticket_status_endpoint_authentication(interceptor_keys):
    """Verify /ticket/{id}/status endpoint enforces cryptographic authentication."""
    import base64
    interceptor_priv, interceptor_pub = interceptor_keys
    other_priv, _ = generate_keypair()

    gw = MockUPIGateway(interceptor_public_key=interceptor_pub)
    client = TestClient(create_app(gw=gw))

    # 1. Unauthenticated request without header -> 403 Forbidden
    resp = client.get("/ticket/tkt-1234/status")
    assert resp.status_code == 403
    assert "Unauthorized" in resp.json()["detail"]

    # 2. Request signed by unauthorized / foreign key -> 403 Forbidden
    foreign_sig = other_priv.sign(b"status:tkt-1234")
    foreign_header = base64.b64encode(foreign_sig).decode("ascii")
    resp_foreign = client.get(
        "/ticket/tkt-1234/status",
        headers={"x-kavach-interceptor-auth": foreign_header},
    )
    assert resp_foreign.status_code == 403

    # 3. Request signed by authorized interceptor key -> 200 OK
    auth_sig = interceptor_priv.sign(b"status:tkt-1234")
    auth_header = base64.b64encode(auth_sig).decode("ascii")
    resp_auth = client.get(
        "/ticket/tkt-1234/status",
        headers={"x-kavach-interceptor-auth": auth_header},
    )
    assert resp_auth.status_code == 200
    assert resp_auth.json() == {"ticket_id": "tkt-1234", "settled": False}




