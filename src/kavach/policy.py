"""Pure policy-check functions — hardened for Phase 2 adversarial threats.

Every function is stateless except check_budget and check_txn_count.
All checks enforce strict types (no booleans, floats, NaN, inf) and exact VPA matching.
"""

from __future__ import annotations

import math
import re
import time
from typing import Any, Optional

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from kavach.models import Decision, MandateClaims, ReasonCode


# ---------------------------------------------------------------------------
# 1. Signature + algorithm pinning
# ---------------------------------------------------------------------------

_ALLOWED_ALGORITHMS = frozenset({"EdDSA"})


def check_signature_and_algorithm(
    token: str, public_key: Ed25519PublicKey
) -> tuple[Decision | None, Optional[MandateClaims]]:
    """Verify JWT signature with algorithm pinning.

    Returns (error_decision, None) on failure or (None, claims) on success.
    """
    if not isinstance(token, str) or not token.strip():
        return Decision.deny(ReasonCode.SIGNATURE_INVALID, "Empty or non-string token"), None

    try:
        header = jwt.get_unverified_header(token)
    except (jwt.exceptions.DecodeError, Exception):
        return Decision.deny(ReasonCode.SIGNATURE_INVALID, "Malformed token header"), None

    alg = header.get("alg", "")
    if alg not in _ALLOWED_ALGORITHMS:
        return (
            Decision.deny(
                ReasonCode.ALGORITHM_NOT_ALLOWED,
                f"Algorithm '{alg}' is not allowed. Only {_ALLOWED_ALGORITHMS} accepted.",
            ),
            None,
        )

    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=list(_ALLOWED_ALGORITHMS),
            options={
                "verify_exp": False,  # we check time window ourselves
                "verify_nbf": False,
            },
        )
    except jwt.exceptions.InvalidSignatureError:
        return Decision.deny(ReasonCode.SIGNATURE_INVALID, "Signature verification failed"), None
    except jwt.exceptions.DecodeError as exc:
        return Decision.deny(ReasonCode.SIGNATURE_INVALID, f"Token decode error: {exc}"), None

    try:
        claims = MandateClaims(**payload)
    except Exception as exc:
        return Decision.deny(ReasonCode.SIGNATURE_INVALID, f"Invalid claims structure: {exc}"), None

    return None, claims


# ---------------------------------------------------------------------------
# 2. Time window
# ---------------------------------------------------------------------------


def check_time_window(claims: MandateClaims, now: Optional[int] = None) -> Decision | None:
    """Check nbf and exp. Returns error Decision or None if OK."""
    now = now or int(time.time())
    if now < claims.nbf:
        return Decision.deny(
            ReasonCode.MANDATE_NOT_YET_VALID,
            f"Mandate not valid until {claims.nbf} (now={now})",
        )
    if now >= claims.exp:
        return Decision.deny(
            ReasonCode.MANDATE_EXPIRED,
            f"Mandate expired at {claims.exp} (now={now})",
        )
    return None


# ---------------------------------------------------------------------------
# 3. Revocation
# ---------------------------------------------------------------------------


def check_revocation(jti: str, revoked_jtis: frozenset[str]) -> Decision | None:
    """Check if the mandate jti has been revoked."""
    if jti in revoked_jtis:
        return Decision.deny(ReasonCode.MANDATE_REVOKED, f"Mandate {jti} has been revoked")
    return None


# ---------------------------------------------------------------------------
# 4. Payee allowlist (Strict Canonicalization)
# ---------------------------------------------------------------------------

# Strict UPI VPA pattern: username@bankhandle (e.g. ondc-grocer@upi)
_VPA_REGEX = re.compile(r"^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{2,64}$")


def check_payee(payee_vpa: str, allowed_payees: list[str]) -> Decision | None:
    """Check if payee is in the mandate's allowed list.

    Enforces exact string matching with no fuzzy normalization.
    Rejects lookalikes, trailing whitespace, invalid characters, and multiple '@'.
    """
    if not isinstance(payee_vpa, str):
        return Decision.deny(ReasonCode.PAYEE_NOT_ALLOWED, "Payee VPA must be a string")

    # Reject leading/trailing whitespace
    if payee_vpa != payee_vpa.strip():
        return Decision.deny(
            ReasonCode.PAYEE_NOT_ALLOWED,
            f"Payee VPA '{payee_vpa}' contains invalid leading/trailing whitespace",
        )

    # Validate UPI syntax
    if not _VPA_REGEX.match(payee_vpa):
        return Decision.deny(
            ReasonCode.PAYEE_NOT_ALLOWED,
            f"Payee VPA '{payee_vpa}' is not a syntactically valid UPI address",
        )

    # Exact equality check — no case-folding or homoglyph normalization
    if payee_vpa not in allowed_payees:
        return Decision.deny(
            ReasonCode.PAYEE_NOT_ALLOWED,
            f"Payee '{payee_vpa}' is not authorized by the mandate",
        )
    return None


# ---------------------------------------------------------------------------
# 5. Category match
# ---------------------------------------------------------------------------


def check_category(category: str, allowed_categories: list[str]) -> Decision | None:
    """Check if the payment category is allowed.

    If allowed_categories is empty, any category is accepted.
    """
    if not allowed_categories:
        return None
    if not isinstance(category, str):
        return Decision.deny(ReasonCode.CATEGORY_NOT_ALLOWED, "Category must be a string")

    if category.lower() not in [c.lower() for c in allowed_categories]:
        return Decision.deny(
            ReasonCode.CATEGORY_NOT_ALLOWED,
            f"Category '{category}' not in allowed list {allowed_categories}",
        )
    return None


# ---------------------------------------------------------------------------
# 6. Amount validity (Strict Integer Paise)
# ---------------------------------------------------------------------------


def check_amount(amount_paise: Any) -> Decision | None:
    """Amount must be a positive integer in paise.

    Explicitly rejects:
    - booleans (isinstance(True, int) is True in Python)
    - floats (e.g. 1200.5, NaN, inf)
    - negative or zero values
    - unreasonably huge numbers (> 1,000,000,000 paise = 1 Crore INR)
    """
    if isinstance(amount_paise, bool):
        return Decision.deny(
            ReasonCode.INVALID_AMOUNT,
            "Boolean value passed as amount (type confusion rejected)",
        )

    if isinstance(amount_paise, float):
        if math.isnan(amount_paise) or math.isinf(amount_paise):
            return Decision.deny(
                ReasonCode.INVALID_AMOUNT,
                "Float NaN/Inf passed as amount",
            )
        return Decision.deny(
            ReasonCode.INVALID_AMOUNT,
            f"Floats not permitted for financial amounts: {amount_paise}",
        )

    if not isinstance(amount_paise, int):
        return Decision.deny(
            ReasonCode.INVALID_AMOUNT,
            f"Amount must be an integer, got {type(amount_paise).__name__}",
        )

    if amount_paise <= 0:
        return Decision.deny(
            ReasonCode.INVALID_AMOUNT,
            f"Amount must be positive integer paise (got {amount_paise})",
        )

    if amount_paise > 1_000_000_000:
        return Decision.deny(
            ReasonCode.INVALID_AMOUNT,
            f"Amount exceeds maximum safe bounds ({amount_paise} > 1,000,000,000 paise)",
        )

    return None


# ---------------------------------------------------------------------------
# 7. Per-transaction limit
# ---------------------------------------------------------------------------


def check_per_txn_limit(amount_paise: int, limit_paise: int) -> Decision | None:
    """Check that the single payment doesn't exceed the per-txn cap."""
    if amount_paise > limit_paise:
        return Decision.deny(
            ReasonCode.AMOUNT_EXCEEDS_PER_TXN_LIMIT,
            f"Amount {amount_paise} paise exceeds per-txn limit of {limit_paise} paise",
        )
    return None


# ---------------------------------------------------------------------------
# 8. Cumulative budget
# ---------------------------------------------------------------------------


def check_budget(amount_paise: int, spent_paise: int, budget_paise: int) -> Decision | None:
    """Check that this payment won't exceed the total budget."""
    if spent_paise + amount_paise > budget_paise:
        return Decision.deny(
            ReasonCode.BUDGET_EXHAUSTED,
            f"Payment of {amount_paise} paise would exceed the cumulative authorized budget",
        )
    return None


# ---------------------------------------------------------------------------
# 9. Transaction count
# ---------------------------------------------------------------------------


def check_txn_count(current_count: int, max_count: int) -> Decision | None:
    """Check that the transaction count hasn't been exceeded."""
    if current_count >= max_count:
        return Decision.deny(
            ReasonCode.TXN_COUNT_EXCEEDED,
            f"Transaction count {current_count} has reached max of {max_count}",
        )
    return None


# ---------------------------------------------------------------------------
# 10. Replay protection (nonce)
# ---------------------------------------------------------------------------


def check_replay(nonce: str, used_nonces: frozenset[str]) -> Decision | None:
    """Check that this nonce hasn't been used before."""
    if not nonce:
        return None  # nonce is optional; if absent, skip replay check
    if nonce in used_nonces:
        return Decision.deny(
            ReasonCode.NONCE_REPLAYED,
            f"Nonce '{nonce}' has already been used",
        )
    return None
