"""Tests for the mandate issuer."""

from __future__ import annotations

import time

import jwt
import pytest

from kavach.issuer import (
    decode_mandate_unsafe,
    generate_keypair,
    load_private_key,
    load_public_key,
    save_keypair,
    sign_mandate,
)
from kavach.models import MandateSpec


class TestKeypairGeneration:
    def test_generate_keypair_types(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
        priv, pub = generate_keypair()
        assert isinstance(priv, Ed25519PrivateKey)
        assert isinstance(pub, Ed25519PublicKey)

    def test_save_and_load_keypair(self, tmp_path):
        priv, pub = generate_keypair()
        priv_path, pub_path = save_keypair(priv, pub, tmp_path)

        loaded_priv = load_private_key(priv_path)
        loaded_pub = load_public_key(pub_path)

        # Sign with original, verify with loaded
        mandate = MandateSpec(
            purpose="test",
            allowed_payees=["test@upi"],
            per_txn_limit_paise=100,
            total_budget_paise=1000,
            max_txn_count=1,
        )
        token = sign_mandate(priv, "agent", mandate)
        decoded = jwt.decode(token, loaded_pub, algorithms=["EdDSA"])
        assert decoded["sub"] == "agent"


class TestSignMandate:
    def test_valid_token_structure(self, private_key, public_key, default_mandate):
        token = sign_mandate(private_key, "agent_001", default_mandate)

        decoded = jwt.decode(token, public_key, algorithms=["EdDSA"])
        assert decoded["iss"] == "user@kavach"
        assert decoded["sub"] == "agent_001"
        assert "jti" in decoded
        assert "iat" in decoded
        assert "nbf" in decoded
        assert "exp" in decoded
        assert "mandate" in decoded

    def test_mandate_claims_are_integers(self, private_key, public_key, default_mandate):
        token = sign_mandate(private_key, "agent_001", default_mandate)
        decoded = jwt.decode(token, public_key, algorithms=["EdDSA"])
        mandate = decoded["mandate"]

        assert isinstance(mandate["per_txn_limit_paise"], int)
        assert isinstance(mandate["total_budget_paise"], int)
        assert isinstance(mandate["max_txn_count"], int)

    def test_custom_jti(self, private_key, default_mandate):
        token = sign_mandate(private_key, "agent_001", default_mandate, jti="custom-id")
        decoded = decode_mandate_unsafe(token)
        assert decoded["jti"] == "custom-id"

    def test_algorithm_is_eddsa(self, private_key, default_mandate):
        token = sign_mandate(private_key, "agent_001", default_mandate)
        header = jwt.get_unverified_header(token)
        assert header["alg"] == "EdDSA"

    def test_nbf_offset(self, private_key, default_mandate):
        token = sign_mandate(
            private_key, "agent_001", default_mandate, nbf_offset=60
        )
        decoded = decode_mandate_unsafe(token)
        assert decoded["nbf"] > decoded["iat"]
