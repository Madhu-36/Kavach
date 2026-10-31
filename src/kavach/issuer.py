"""Mandate Issuer — Ed25519 keypair generation, JWT mandate signing,
and interceptor approval ticket signing/verification.

The user's PRIVATE key lives only on the user side.
The interceptor's PRIVATE key lives only in the interceptor service.
The agent never touches any private keys or tokens.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from kavach.models import ApprovalTicketPayload, MandateSpec


def generate_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate a fresh Ed25519 keypair."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    return private_key, public_key


def save_keypair(
    private_key: Ed25519PrivateKey,
    public_key: Ed25519PublicKey,
    directory: Path,
    prefix: str = "kavach",
) -> tuple[Path, Path]:
    """Save keypair to PEM files. Returns (private_path, public_path)."""
    directory.mkdir(parents=True, exist_ok=True)

    priv_path = directory / f"{prefix}_private.pem"
    pub_path = directory / f"{prefix}_public.pem"

    priv_pem = private_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    pub_pem = public_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)

    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)

    return priv_path, pub_path


def load_private_key(path: Path) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from a PEM file."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("Key is not Ed25519")
    return key


def load_public_key(path: Path) -> Ed25519PublicKey:
    """Load an Ed25519 public key from a PEM file."""
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    key = load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError("Key is not Ed25519")
    return key


def private_key_to_pem(key: Ed25519PrivateKey) -> bytes:
    """Serialize private key to PEM bytes."""
    return key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())


def public_key_to_pem(key: Ed25519PublicKey) -> bytes:
    """Serialize public key to PEM bytes."""
    return key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)


def sign_mandate(
    private_key: Ed25519PrivateKey,
    agent_id: str,
    mandate: MandateSpec,
    issuer: str = "user@kavach",
    validity_seconds: int = 900,
    nbf_offset: int = 0,
    jti: Optional[str] = None,
) -> str:
    """Sign a mandate JWT with the user's Ed25519 private key."""
    now = int(time.time())
    payload = {
        "iss": issuer,
        "sub": agent_id,
        "jti": jti or str(uuid.uuid4()),
        "iat": now,
        "nbf": now + nbf_offset,
        "exp": now + validity_seconds,
        "mandate": mandate.model_dump(),
    }

    token: str = jwt.encode(payload, private_key, algorithm="EdDSA")
    return token


def sign_approval_ticket(
    interceptor_private_key: Ed25519PrivateKey,
    mandate_jti: str,
    payee_vpa: str,
    amount_paise: int,
    nonce: str,
    validity_seconds: int = 30,
) -> str:
    """Sign an ephemeral, per-transaction approval ticket using the interceptor key."""
    now = int(time.time())
    payload = {
        "iss": "kavach-interceptor",
        "sub": mandate_jti,
        "jti": str(uuid.uuid4()),
        "payee_vpa": payee_vpa,
        "amount_paise": amount_paise,
        "nonce": nonce,
        "iat": now,
        "exp": now + validity_seconds,
    }
    ticket: str = jwt.encode(payload, interceptor_private_key, algorithm="EdDSA")
    return ticket


def verify_approval_ticket(
    ticket: str,
    interceptor_public_key: Ed25519PublicKey,
) -> tuple[bool, str, Optional[ApprovalTicketPayload]]:
    """Verify an approval ticket's signature, algorithm pinning, and expiry."""
    try:
        header = jwt.get_unverified_header(ticket)
        if header.get("alg") != "EdDSA":
            return False, f"Invalid ticket algorithm: {header.get('alg')}", None

        claims = jwt.decode(
            ticket,
            interceptor_public_key,
            algorithms=["EdDSA"],
            options={"verify_exp": True},
        )
        return True, "Valid", ApprovalTicketPayload(**claims)
    except jwt.exceptions.ExpiredSignatureError:
        return False, "Approval ticket expired", None
    except jwt.exceptions.InvalidSignatureError:
        return False, "Approval ticket signature invalid", None
    except Exception as exc:
        return False, f"Ticket verification error: {exc}", None


def decode_mandate_unsafe(token: str) -> dict:
    """Decode a mandate JWT WITHOUT verifying the signature."""
    return jwt.decode(
        token,
        options={
            "verify_signature": False,
            "verify_exp": False,
            "verify_nbf": False,
        },
        algorithms=["EdDSA"],
    )
