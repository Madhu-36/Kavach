"""Kavach Hierarchical Delegation — Attenuated Sub-Mandates.

Enables multi-agent delegation where a parent coordinator agent delegates
limited spending authority to a sub-agent (e.g., delivery worker).

Core Attenuation Invariants (Fail-Closed):
1. Sub-agent allowed_payees ⊆ Parent allowed_payees
2. Sub-agent allowed_categories ⊆ Parent allowed_categories
3. Sub-agent per_txn_limit_paise ≤ Parent per_txn_limit_paise
4. Sub-agent total_budget_paise ≤ Parent total_budget_paise
5. Sub-agent exp ≤ Parent exp
6. Sub-agent max_txn_count ≤ Parent max_txn_count
7. Cryptographic Chain: Root User Key → Parent Agent Key → Sub-Agent Request
8. Dual Drawdown: Sub-agent spend simultaneously reserves from the child ledger
   AND the parent root mandate ledger, guaranteeing zero aggregate budget leak.
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict

from kavach.issuer import sign_mandate
from kavach.models import Decision, MandateClaims, MandateSpec, PaymentRequest, ReasonCode


class DelegationAttenuationError(ValueError):
    """Raised when a sub-mandate attempts to widen parental scope."""


class SubMandateSpec(BaseModel):
    """Specification for an attenuated sub-mandate."""

    purpose: str
    allowed_payees: list[str]
    allowed_categories: list[str]
    per_txn_limit_paise: int
    total_budget_paise: int
    max_txn_count: int

    model_config = ConfigDict(extra="forbid")


def validate_attenuation(
    root_spec: MandateSpec,
    child_spec: SubMandateSpec,
    root_exp: int,
    child_exp: int,
) -> None:
    """Validate that child_spec strictly attenuates root_spec (monotonic narrowing)."""
    # 1. Payees must be a subset
    root_payees = set(root_spec.allowed_payees)
    child_payees = set(child_spec.allowed_payees)
    if not child_payees.issubset(root_payees):
        forbidden = child_payees - root_payees
        raise DelegationAttenuationError(
            f"Child mandate attempts to expand payees with unauthorized VPAs: {forbidden}"
        )

    # 2. Categories must be a subset
    root_cats = set(root_spec.allowed_categories)
    child_cats = set(child_spec.allowed_categories)
    if not child_cats.issubset(root_cats):
        forbidden = child_cats - root_cats
        raise DelegationAttenuationError(
            f"Child mandate attempts to expand categories: {forbidden}"
        )

    # 3. Per-txn limit <= parent
    if child_spec.per_txn_limit_paise > root_spec.per_txn_limit_paise:
        raise DelegationAttenuationError(
            f"Child per-txn limit ({child_spec.per_txn_limit_paise}) exceeds parent ({root_spec.per_txn_limit_paise})"
        )

    # 4. Total budget <= parent
    if child_spec.total_budget_paise > root_spec.total_budget_paise:
        raise DelegationAttenuationError(
            f"Child total budget ({child_spec.total_budget_paise}) exceeds parent ({root_spec.total_budget_paise})"
        )

    # 5. Expiry <= parent expiry
    if child_exp > root_exp:
        raise DelegationAttenuationError(
            f"Child expiry ({child_exp}) extends beyond parent expiry ({root_exp})"
        )

    # 6. Max txn count <= parent
    if child_spec.max_txn_count > root_spec.max_txn_count:
        raise DelegationAttenuationError(
            f"Child transaction count ({child_spec.max_txn_count}) exceeds parent ({root_spec.max_txn_count})"
        )


def issue_sub_mandate(
    parent_private_key: Ed25519PrivateKey,
    parent_mandate_token: str,
    child_agent_id: str,
    child_spec: SubMandateSpec,
    validity_seconds: int = 600,
) -> str:
    """Issue an attenuated sub-mandate signed by the parent agent."""
    parent_payload = jwt.decode(parent_mandate_token, options={"verify_signature": False})
    root_spec = MandateSpec(**parent_payload["mandate"])
    root_exp = parent_payload["exp"]

    now = int(time.time())
    child_exp = now + validity_seconds

    # Enforce attenuation at issuance time
    validate_attenuation(root_spec, child_spec, root_exp, child_exp)

    payload = {
        "iss": parent_payload.get("sub", "parent_agent"),
        "sub": child_agent_id,
        "jti": str(uuid.uuid4()),
        "parent_jti": parent_payload["jti"],
        "iat": now,
        "nbf": now,
        "exp": child_exp,
        "mandate": child_spec.model_dump(),
    }

    token: str = jwt.encode(payload, parent_private_key, algorithm="EdDSA")
    return token


def verify_delegation_chain(
    child_token: str,
    root_token: str,
    user_public_key: Ed25519PublicKey,
    parent_public_key: Ed25519PublicKey,
) -> tuple[bool, Optional[str], Optional[dict]]:
    """Cryptographically verify delegation chain from User -> Parent -> Child.

    Returns (is_valid, error_reason, child_payload).
    """
    try:
        # 1. Verify root token with user public key
        root_payload = jwt.decode(
            root_token,
            user_public_key,
            algorithms=["EdDSA"],
            options={"require": ["iss", "sub", "jti", "nbf", "exp", "mandate"]},
        )
        root_spec = MandateSpec(**root_payload["mandate"])
    except Exception as exc:
        return False, f"Root mandate signature/payload invalid: {exc}", None

    try:
        # 2. Verify child token with parent public key
        child_payload = jwt.decode(
            child_token,
            parent_public_key,
            algorithms=["EdDSA"],
            options={"require": ["iss", "sub", "jti", "parent_jti", "nbf", "exp", "mandate"]},
        )
        child_spec = SubMandateSpec(**child_payload["mandate"])
    except Exception as exc:
        return False, f"Child mandate signature/payload invalid: {exc}", None

    # 3. Verify linkage
    if child_payload["parent_jti"] != root_payload["jti"]:
        return False, "Delegation chain mismatch: child parent_jti does not match root jti", None

    # 4. Verify attenuation invariants
    try:
        validate_attenuation(root_spec, child_spec, root_payload["exp"], child_payload["exp"])
    except DelegationAttenuationError as att_err:
        return False, str(att_err), None

    return True, None, child_payload
