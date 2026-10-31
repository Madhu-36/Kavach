"""Shared test fixtures for Kavach."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kavach.audit import AuditLog
from kavach.interceptor import KavachInterceptor
from kavach.issuer import generate_keypair, public_key_to_pem, sign_mandate
from kavach.ledger import Ledger
from kavach.models import MandateSpec


@pytest.fixture
def keypair():
    """Generate a fresh Ed25519 keypair."""
    return generate_keypair()


@pytest.fixture
def private_key(keypair):
    return keypair[0]


@pytest.fixture
def public_key(keypair):
    return keypair[1]


@pytest.fixture
def default_mandate() -> MandateSpec:
    """Standard test mandate: ₹1,500 per-txn, ₹3,000 budget, 5 txns."""
    return MandateSpec(
        purpose="grocery shopping",
        allowed_payees=["ondc-grocer@upi"],
        allowed_categories=["groceries", "essentials"],
        per_txn_limit_paise=150000,
        total_budget_paise=300000,
        max_txn_count=5,
    )


@pytest.fixture
def valid_token(private_key, default_mandate):
    """A valid mandate token with 15-minute validity."""
    return sign_mandate(
        private_key=private_key,
        agent_id="agent_001",
        mandate=default_mandate,
        validity_seconds=900,
    )


@pytest.fixture
def ledger(tmp_path):
    return Ledger(persist_path=tmp_path / "ledger.json")


@pytest.fixture
def audit_log(tmp_path):
    return AuditLog(path=tmp_path / "audit.jsonl")


@pytest.fixture
def interceptor(public_key, valid_token, ledger, audit_log):
    """A standard interceptor with valid config."""
    return KavachInterceptor(
        public_key=public_key,
        mandate_token=valid_token,
        ledger=ledger,
        audit_log=audit_log,
    )
