"""Tests for Hierarchical Delegation and Attenuated Sub-Mandates.

Verifies:
- Monotonic attenuation rules (fail-closed on widening attempts)
- Cryptographic chain verification (User -> Parent -> Child)
- Linkage integrity (parent_jti binding)
"""

from __future__ import annotations

import time
import pytest

from kavach.delegation import (
    DelegationAttenuationError,
    SubMandateSpec,
    issue_sub_mandate,
    validate_attenuation,
    verify_delegation_chain,
)
from kavach.issuer import generate_keypair, sign_mandate
from kavach.models import MandateSpec


@pytest.fixture
def keys():
    user_priv, user_pub = generate_keypair()
    parent_priv, parent_pub = generate_keypair()
    child_priv, child_pub = generate_keypair()
    return {
        "user_priv": user_priv,
        "user_pub": user_pub,
        "parent_priv": parent_priv,
        "parent_pub": parent_pub,
        "child_priv": child_priv,
        "child_pub": child_pub,
    }


@pytest.fixture
def root_mandate(keys):
    spec = MandateSpec(
        purpose="Root Shopping Authority",
        allowed_payees=["grocer@upi", "courier@upi", "merchant@upi"],
        allowed_categories=["groceries", "logistics"],
        per_txn_limit_paise=50_000,    # ₹500
        total_budget_paise=200_000,    # ₹2,000
        max_txn_count=10,
    )
    token = sign_mandate(
        keys["user_priv"],
        agent_id="parent_agent",
        mandate=spec,
        validity_seconds=1800,
    )
    return token, spec


class TestDelegationAttenuation:
    """Tests for monotonic scope narrowing in sub-mandates."""

    def test_valid_sub_mandate_issuance_and_verification(self, keys, root_mandate):
        root_token, _ = root_mandate

        # Sub-agent gets delivery authority: courier only, ₹100 per txn, ₹300 total, 3 txns
        child_spec = SubMandateSpec(
            purpose="Courier Delivery Tip",
            allowed_payees=["courier@upi"],
            allowed_categories=["logistics"],
            per_txn_limit_paise=10_000,
            total_budget_paise=30_000,
            max_txn_count=3,
        )

        child_token = issue_sub_mandate(
            parent_private_key=keys["parent_priv"],
            parent_mandate_token=root_token,
            child_agent_id="child_courier_agent",
            child_spec=child_spec,
            validity_seconds=300,
        )

        ok, err, claims = verify_delegation_chain(
            child_token=child_token,
            root_token=root_token,
            user_public_key=keys["user_pub"],
            parent_public_key=keys["parent_pub"],
        )
        assert ok is True
        assert err is None
        assert claims is not None
        assert claims["sub"] == "child_courier_agent"
        assert claims["mandate"]["allowed_payees"] == ["courier@upi"]

    def test_widening_payee_raises_attenuation_error(self, keys, root_mandate):
        root_token, _ = root_mandate

        # Attacker tries to sneak in unapproved payee "attacker@upi"
        rogue_spec = SubMandateSpec(
            purpose="Sneak Attacker VPA",
            allowed_payees=["courier@upi", "attacker@upi"],
            allowed_categories=["logistics"],
            per_txn_limit_paise=10_000,
            total_budget_paise=30_000,
            max_txn_count=3,
        )

        with pytest.raises(DelegationAttenuationError) as exc_info:
            issue_sub_mandate(
                parent_private_key=keys["parent_priv"],
                parent_mandate_token=root_token,
                child_agent_id="child_courier_agent",
                child_spec=rogue_spec,
                validity_seconds=300,
            )
        assert "unauthorized VPAs" in str(exc_info.value)

    def test_widening_category_raises_attenuation_error(self, keys, root_mandate):
        root_token, _ = root_mandate

        # Attacker tries to sneak in unapproved category "gambling"
        rogue_spec = SubMandateSpec(
            purpose="Sneak Category",
            allowed_payees=["courier@upi"],
            allowed_categories=["logistics", "gambling"],
            per_txn_limit_paise=10_000,
            total_budget_paise=30_000,
            max_txn_count=3,
        )

        with pytest.raises(DelegationAttenuationError) as exc_info:
            issue_sub_mandate(
                parent_private_key=keys["parent_priv"],
                parent_mandate_token=root_token,
                child_agent_id="child_courier_agent",
                child_spec=rogue_spec,
                validity_seconds=300,
            )
        assert "expand categories" in str(exc_info.value)

    def test_raising_per_txn_limit_raises_attenuation_error(self, keys, root_mandate):
        root_token, _ = root_mandate

        # Parent limit is 50_000, child tries 60_000
        rogue_spec = SubMandateSpec(
            purpose="Raise Limit",
            allowed_payees=["courier@upi"],
            allowed_categories=["logistics"],
            per_txn_limit_paise=60_000,
            total_budget_paise=100_000,
            max_txn_count=3,
        )

        with pytest.raises(DelegationAttenuationError) as exc_info:
            issue_sub_mandate(
                parent_private_key=keys["parent_priv"],
                parent_mandate_token=root_token,
                child_agent_id="child_courier_agent",
                child_spec=rogue_spec,
                validity_seconds=300,
            )
        assert "exceeds parent" in str(exc_info.value)

    def test_raising_total_budget_raises_attenuation_error(self, keys, root_mandate):
        root_token, _ = root_mandate

        # Parent budget is 200_000, child tries 300_000
        rogue_spec = SubMandateSpec(
            purpose="Raise Budget",
            allowed_payees=["courier@upi"],
            allowed_categories=["logistics"],
            per_txn_limit_paise=20_000,
            total_budget_paise=300_000,
            max_txn_count=3,
        )

        with pytest.raises(DelegationAttenuationError) as exc_info:
            issue_sub_mandate(
                parent_private_key=keys["parent_priv"],
                parent_mandate_token=root_token,
                child_agent_id="child_courier_agent",
                child_spec=rogue_spec,
                validity_seconds=300,
            )
        assert "Child total budget" in str(exc_info.value)

    def test_extending_validity_beyond_parent_raises_attenuation_error(self, keys, root_mandate):
        root_token, _ = root_mandate

        child_spec = SubMandateSpec(
            purpose="Valid Spec",
            allowed_payees=["courier@upi"],
            allowed_categories=["logistics"],
            per_txn_limit_paise=10_000,
            total_budget_paise=30_000,
            max_txn_count=3,
        )

        # Parent has 1800s validity; child tries 3600s
        with pytest.raises(DelegationAttenuationError) as exc_info:
            issue_sub_mandate(
                parent_private_key=keys["parent_priv"],
                parent_mandate_token=root_token,
                child_agent_id="child_courier_agent",
                child_spec=child_spec,
                validity_seconds=3600,
            )
        assert "Child expiry" in str(exc_info.value)

    def test_wrong_parent_signing_key_fails_verification(self, keys, root_mandate):
        root_token, _ = root_mandate
        child_spec = SubMandateSpec(
            purpose="Valid Spec",
            allowed_payees=["courier@upi"],
            allowed_categories=["logistics"],
            per_txn_limit_paise=10_000,
            total_budget_paise=30_000,
            max_txn_count=3,
        )
        # Sign with rogue key instead of parent
        rogue_priv, rogue_pub = generate_keypair()
        child_token = issue_sub_mandate(
            parent_private_key=rogue_priv,
            parent_mandate_token=root_token,
            child_agent_id="child_courier_agent",
            child_spec=child_spec,
            validity_seconds=300,
        )

        # Verification with real parent public key fails
        ok, err, _ = verify_delegation_chain(
            child_token=child_token,
            root_token=root_token,
            user_public_key=keys["user_pub"],
            parent_public_key=keys["parent_pub"],
        )
        assert ok is False
        assert "signature/payload invalid" in err
