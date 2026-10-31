"""Tests for the agent — especially that the mandate token is NEVER
visible to the LLM in tool schemas, prompts, or context."""

from __future__ import annotations

import pytest

from kavach.agent import ScriptedCompromisedAgent, get_tool_schemas, make_upi_payment_tool
from kavach.interceptor import KavachInterceptor
from kavach.issuer import generate_keypair, sign_mandate
from kavach.ledger import Ledger
from kavach.audit import AuditLog
from kavach.models import MandateSpec, PaymentRequest, ReasonCode


class TestTokenNotInPrompt:
    """Assert the mandate token never appears in the LLM tool schema or prompt."""

    def test_token_not_in_tool_schemas(self, valid_token):
        schemas = get_tool_schemas()
        for name, schema in schemas.items():
            for field in ["doc", "signature", "source"]:
                assert valid_token not in schema[field], (
                    f"Token found in tool schema '{name}'.{field}!"
                )
                assert "mandate" not in schema[field].lower() or "mandate" in (
                    # allow the word 'mandate' only in kavach module-level references,
                    # not in the actual tool function source
                    "make_upi_payment_tool"
                ), f"Mandate reference found in tool '{name}'"

    def test_token_not_in_tool_function_source(self, valid_token):
        """The tool function source must not contain any token strings."""
        import inspect
        source = inspect.getsource(make_upi_payment_tool)
        assert "mandate_token" not in source
        assert "private_key" not in source
        assert "public_key" not in source
        assert valid_token not in source


class TestScriptedCompromisedAgent:
    """Test the scripted-compromised agent with Kavach interceptor."""

    @pytest.fixture
    def setup(self):
        priv, pub = generate_keypair()
        mandate = MandateSpec(
            purpose="grocery shopping",
            allowed_payees=["ondc-grocer@upi"],
            allowed_categories=["groceries"],
            per_txn_limit_paise=150000,
            total_budget_paise=300000,
            max_txn_count=5,
        )
        token = sign_mandate(priv, "agent_001", mandate)
        ledger = Ledger()
        audit = AuditLog()
        interceptor = KavachInterceptor(
            public_key=pub,
            mandate_token=token,
            ledger=ledger,
            audit_log=audit,
        )

        def payment_fn(amount_paise, payee_vpa, note="", category="groceries"):
            req = PaymentRequest(
                amount_paise=amount_paise,
                payee_vpa=payee_vpa,
                note=note,
                category=category,
            )
            decision, _ = interceptor.evaluate(req)
            if decision.allowed:
                return f"✓ APPROVED: {decision.message}"
            else:
                codes = [decision.reason_code] + decision.reason_codes
                return f"✗ BLOCKED: {', '.join(str(c.value) for c in codes)} — {decision.message}"

        return interceptor, payment_fn, ledger

    def test_legitimate_purchase_approved(self, setup):
        interceptor, payment_fn, ledger = setup
        agent = ScriptedCompromisedAgent(payment_fn=payment_fn)
        actions = agent.run_legitimate_only()
        assert "APPROVED" in actions[0]["output"]

    def test_injection_attack_blocked(self, setup):
        interceptor, payment_fn, ledger = setup
        agent = ScriptedCompromisedAgent(payment_fn=payment_fn)
        actions = agent.run_injection_only()
        attack_result = actions[-1]["output"]
        assert "BLOCKED" in attack_result
        assert "PAYEE_NOT_ALLOWED" in attack_result

    def test_full_scenario(self, setup):
        interceptor, payment_fn, ledger = setup
        agent = ScriptedCompromisedAgent(payment_fn=payment_fn)
        actions = agent.run_shopping_task()
        # Legitimate purchase should succeed
        assert "APPROVED" in actions[1]["output"]
        # Attack should fail
        assert "BLOCKED" in actions[2]["output"]
