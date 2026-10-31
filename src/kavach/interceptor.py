"""Kavach Interceptor — the security layer.

Hardened for Phase 3:
- Independent evaluation of all policy checks (reports all violated reason codes).
- Prevents information leakage to agent (no allowlists or remaining budget amounts in error messages).
- Atomic SQLite ledger reservation.
- Issues Ed25519-signed ephemeral approval tickets upon approval.
"""

from __future__ import annotations

import uuid
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from kavach.audit import AuditLog
from kavach.issuer import sign_approval_ticket
from kavach.ledger import Ledger
from kavach.ledger_db import SqliteLedger
from kavach.models import Decision, MandateClaims, PaymentRequest, ReasonCode
from kavach.policy import (
    check_amount,
    check_budget,
    check_category,
    check_payee,
    check_per_txn_limit,
    check_replay,
    check_revocation,
    check_signature_and_algorithm,
    check_time_window,
    check_txn_count,
)


class KavachInterceptor:
    """Cryptographic policy enforcement layer."""

    def __init__(
        self,
        public_key: Ed25519PublicKey,
        mandate_token: str,
        ledger: Optional[Ledger | SqliteLedger] = None,
        audit_log: Optional[AuditLog] = None,
        revoked_jtis: Optional[frozenset[str]] = None,
        interceptor_private_key: Optional[Ed25519PrivateKey] = None,
        root_mandate_token: Optional[str] = None,
        root_user_public_key: Optional[Ed25519PublicKey] = None,
    ) -> None:
        self._public_key = public_key
        self._mandate_token = mandate_token
        self._ledger = ledger or SqliteLedger()
        self._audit_log = audit_log or AuditLog()
        self._revoked_jtis = revoked_jtis or frozenset()
        self._used_nonces: set[str] = set()
        self._interceptor_private_key = interceptor_private_key
        self._root_mandate_token = root_mandate_token
        self._root_user_public_key = root_user_public_key

    @property
    def mandate_token(self) -> str:
        return self._mandate_token

    def update_revoked_jtis(self, revoked: frozenset[str]) -> None:
        """Update the revocation list."""
        self._revoked_jtis = revoked

    def evaluate(self, request: PaymentRequest) -> tuple[Decision, Optional[MandateClaims]]:
        """Run the full check chain. Returns (decision, claims_if_parsed).

        Evaluates all independent policy checks to record every violated reason code.
        Fails closed on any error.
        """
        claims: Optional[MandateClaims] = None
        idempotency_key = request.idempotency_key or request.nonce or str(uuid.uuid4())
        reservation_made = False
        child_payload: Optional[dict] = None
        parent_idempotency_key: Optional[str] = None
        parent_reservation_made = False

        try:
            # 0. Delegation chain verification if root token provided
            if self._root_mandate_token and self._root_user_public_key:
                from kavach.delegation import verify_delegation_chain
                ok_del, err_del, child_payload = verify_delegation_chain(
                    child_token=self._mandate_token,
                    root_token=self._root_mandate_token,
                    user_public_key=self._root_user_public_key,
                    parent_public_key=self._public_key,
                )
                if not ok_del:
                    decision = Decision.deny(
                        ReasonCode.DELEGATION_INVALID,
                        f"Delegation chain validation failed: {err_del}",
                    )
                    self._log(request, decision, "delegated_child")
                    return decision, None

            # 1. Signature + algorithm pinning
            err, claims = check_signature_and_algorithm(self._mandate_token, self._public_key)
            if err:
                self._log(request, err, "unknown")
                return err, None

            assert claims is not None
            mandate = claims.mandate

            # 2. Time window (nbf / exp)
            err_time = check_time_window(claims)

            # 3. Revocation
            err_revocation = check_revocation(claims.jti, self._revoked_jtis)

            # 4. Payee allowlist (strict match)
            err_payee = check_payee(request.payee_vpa, mandate.allowed_payees)

            # 5. Category match
            err_category = check_category(request.category, mandate.allowed_categories)

            # 6. Amount validity
            err_amount = check_amount(request.amount_paise)

            # 7. Per-txn limit
            err_per_txn = check_per_txn_limit(request.amount_paise, mandate.per_txn_limit_paise)

            # 8. Replay protection (nonce)
            err_replay = check_replay(request.nonce, frozenset(self._used_nonces))

            # 9. Static cumulative budget check
            spent = self._ledger.get_spent(claims.jti)
            err_budget = check_budget(request.amount_paise, spent, mandate.total_budget_paise)

            # 10. Static transaction count check
            count = self._ledger.get_count(claims.jti)
            err_count = check_txn_count(count, mandate.max_txn_count)

            # Collect all failures
            all_checks = [
                err_time,
                err_revocation,
                err_payee,
                err_category,
                err_amount,
                err_per_txn,
                err_budget,
                err_count,
                err_replay,
            ]
            failures: list[tuple[ReasonCode, str]] = [
                (err.reason_code, err.message) for err in all_checks if err is not None
            ]

            if failures:
                reason_codes = [rc for rc, _ in failures]
                # Safe, non-leaking message for agent
                messages = [msg for _, msg in failures]
                decision = Decision.deny_multi(
                    reason_codes,
                    "; ".join(messages),
                )
                self._log(request, decision, claims.jti)
                return decision, claims

            # Dynamic reservation check (for high-concurrency safety)
            if isinstance(self._ledger, SqliteLedger):
                ok, res_msg = self._ledger.reserve(
                    jti=claims.jti,
                    amount_paise=request.amount_paise,
                    max_budget_paise=mandate.total_budget_paise,
                    max_txn_count=mandate.max_txn_count,
                    idempotency_key=idempotency_key,
                )
                if not ok:
                    rc = (
                        ReasonCode.BUDGET_EXHAUSTED
                        if res_msg == "BUDGET_EXHAUSTED"
                        else ReasonCode.TXN_COUNT_EXCEEDED
                    )
                    decision = Decision.deny(
                        rc,
                        f"Payment of {request.amount_paise} paise exceeds authorized limits.",
                    )
                    self._log(request, decision, claims.jti)
                    return decision, claims
                reservation_made = True

                # Dual Drawdown: If delegated, also reserve against parent root mandate
                if child_payload and "parent_jti" in child_payload and self._root_mandate_token:
                    import jwt
                    parent_jti = child_payload["parent_jti"]
                    parent_idempotency_key = f"parent_{idempotency_key}"
                    root_raw = jwt.decode(self._root_mandate_token, options={"verify_signature": False})
                    root_mandate = root_raw["mandate"]
                    p_ok, p_msg = self._ledger.reserve(
                        jti=parent_jti,
                        amount_paise=request.amount_paise,
                        max_budget_paise=root_mandate["total_budget_paise"],
                        max_txn_count=root_mandate["max_txn_count"],
                        idempotency_key=parent_idempotency_key,
                    )
                    if not p_ok:
                        # Rollback child reservation
                        self._ledger.rollback(idempotency_key)
                        reservation_made = False
                        rc = (
                            ReasonCode.BUDGET_EXHAUSTED
                            if p_msg == "BUDGET_EXHAUSTED"
                            else ReasonCode.TXN_COUNT_EXCEEDED
                        )
                        decision = Decision.deny(
                            rc,
                            f"Payment of {request.amount_paise} paise exceeds root parent budget.",
                        )
                        self._log(request, decision, claims.jti)
                        return decision, claims
                    parent_reservation_made = True

            # All checks passed — generate signed approval ticket if private key available
            approval_ticket: Optional[str] = None
            ticket_id: Optional[str] = None

            if self._interceptor_private_key:
                approval_ticket = sign_approval_ticket(
                    interceptor_private_key=self._interceptor_private_key,
                    mandate_jti=claims.jti,
                    payee_vpa=request.payee_vpa,
                    amount_paise=request.amount_paise,
                    nonce=request.nonce or idempotency_key,
                    validity_seconds=30,
                )
                import jwt

                ticket_payload = jwt.decode(approval_ticket, options={"verify_signature": False})
                ticket_id = ticket_payload.get("jti")

            decision = Decision.approve(
                message=f"Payment of {request.amount_paise} paise to {request.payee_vpa} approved",
                approval_ticket=approval_ticket,
            )

            # Commit the transaction
            if isinstance(self._ledger, SqliteLedger):
                self._ledger.commit(idempotency_key)
                if parent_reservation_made and parent_idempotency_key:
                    self._ledger.commit(parent_idempotency_key)
            else:
                self._ledger.record_txn(claims.jti, request.amount_paise)
                if child_payload and "parent_jti" in child_payload:
                    self._ledger.record_txn(child_payload["parent_jti"], request.amount_paise)

            if request.nonce:
                self._used_nonces.add(request.nonce)

            self._log(request, decision, claims.jti, approval_ticket_id=ticket_id)
            return decision, claims

        except Exception as exc:
            # FAIL CLOSED
            if reservation_made and isinstance(self._ledger, SqliteLedger):
                self._ledger.rollback(idempotency_key)
            if parent_reservation_made and parent_idempotency_key and isinstance(self._ledger, SqliteLedger):
                self._ledger.rollback(parent_idempotency_key)

            decision = Decision.deny(
                ReasonCode.INTERNAL_ERROR,
                f"Internal error (fail-closed): {exc}",
            )
            self._log(request, decision, claims.jti if claims else "unknown")
            return decision, claims

    def _log(
        self,
        request: PaymentRequest,
        decision: Decision,
        jti: str,
        approval_ticket_id: Optional[str] = None,
    ) -> None:
        """Write decision to the audit log."""
        self._audit_log.log_decision(
            mandate_jti=jti,
            payment=request,
            decision=decision,
            approval_ticket_id=approval_ticket_id,
        )
