"""Mock UPI Gateway — FastAPI app simulating a UPI payment switch.

Hardened for Phase 3 (Strict Zero Shared-Secret Policy):
- Fails closed if interceptor public key is missing.
- Verifies Ed25519 per-transaction signed approval tickets.
- Enforces single-use ticket replay protection.
- Validates exact amount and payee matching between request and ticket.
"""

from __future__ import annotations

import time
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from kavach.issuer import verify_approval_ticket


class PayRequest(BaseModel):
    payer_vpa: str
    payee_vpa: str
    amount_paise: int
    note: str = ""
    txn_id: str = ""


class PayResponse(BaseModel):
    success: bool
    message: str
    txn_id: str
    payer_balance_paise: int
    payee_balance_paise: int


class BalanceResponse(BaseModel):
    vpa: str
    balance_paise: int


class MockUPIGateway:
    """In-memory UPI gateway with balance tracking and ticket verification."""

    def __init__(self, interceptor_public_key: Optional[Ed25519PublicKey] = None) -> None:
        self._balances: dict[str, int] = {}
        self._interceptor_public_key = interceptor_public_key
        self._used_tickets: set[str] = set()

    def set_interceptor_public_key(self, public_key: Ed25519PublicKey) -> None:
        self._interceptor_public_key = public_key

    def set_balance(self, vpa: str, amount_paise: int) -> None:
        self._balances[vpa] = amount_paise

    def get_balance(self, vpa: str) -> int:
        return self._balances.get(vpa, 0)

    def verify_ticket(
        self, ticket_token: str, req_payee_vpa: str, req_amount_paise: int
    ) -> tuple[bool, str]:
        """Verify the cryptographic ticket signed by the interceptor."""
        # FAIL CLOSED: If no key configured, reject all payments unconditionally
        if not self._interceptor_public_key:
            return False, "Gateway misconfigured: interceptor public key missing (fail-closed)"

        if not ticket_token or not ticket_token.strip():
            return False, "Missing approval ticket"

        is_valid, msg, payload = verify_approval_ticket(ticket_token, self._interceptor_public_key)
        if not is_valid or not payload:
            return False, f"Ticket signature/expiry error: {msg}"

        # Replay protection on ticket jti
        if payload.jti in self._used_tickets:
            return False, "Approval ticket has already been used (replay detected)"

        # Check parameter binding
        if payload.payee_vpa != req_payee_vpa:
            return (
                False,
                f"Ticket payee '{payload.payee_vpa}' does not match request payee '{req_payee_vpa}'",
            )

        if payload.amount_paise != req_amount_paise:
            return (
                False,
                f"Ticket amount {payload.amount_paise} does not match request amount {req_amount_paise}",
            )

        self._used_tickets.add(payload.jti)
        return True, "Ticket verified"

    def process_payment(
        self,
        payer_vpa: str,
        payee_vpa: str,
        amount_paise: int,
        note: str = "",
        txn_id: str = "",
    ) -> PayResponse:
        if amount_paise <= 0:
            return PayResponse(
                success=False,
                message="Invalid amount",
                txn_id=txn_id,
                payer_balance_paise=self.get_balance(payer_vpa),
                payee_balance_paise=self.get_balance(payee_vpa),
            )

        payer_bal = self._balances.get(payer_vpa, 0)
        if payer_bal < amount_paise:
            return PayResponse(
                success=False,
                message="Insufficient balance",
                txn_id=txn_id,
                payer_balance_paise=payer_bal,
                payee_balance_paise=self.get_balance(payee_vpa),
            )

        self._balances[payer_vpa] = payer_bal - amount_paise
        self._balances[payee_vpa] = self._balances.get(payee_vpa, 0) + amount_paise

        return PayResponse(
            success=True,
            message=f"₹{amount_paise / 100:.2f} sent to {payee_vpa}",
            txn_id=txn_id,
            payer_balance_paise=self._balances[payer_vpa],
            payee_balance_paise=self._balances[payee_vpa],
        )

    def reset(self) -> None:
        self._balances.clear()
        self._used_tickets.clear()


# Default singleton gateway instance
gateway = MockUPIGateway()


def create_app(
    gw: Optional[MockUPIGateway] = None,
    gateway: Optional[MockUPIGateway] = None,
) -> FastAPI:
    """Create a FastAPI app wrapping a gateway instance."""
    _gw = gw or gateway or MockUPIGateway()

    app = FastAPI(title="Mock UPI Gateway", version="0.3.0")

    @app.post("/pay", response_model=PayResponse)
    def pay(
        req: PayRequest,
        x_kavach_approval_ticket: str = Header(default=""),
        x_kavach_approval: str = Header(default=""),
    ) -> PayResponse:
        ticket = x_kavach_approval_ticket or x_kavach_approval
        if not ticket:
            raise HTTPException(status_code=403, detail="Missing approval ticket header")

        valid, reason = _gw.verify_ticket(ticket, req.payee_vpa, req.amount_paise)
        if not valid:
            raise HTTPException(status_code=403, detail=f"Approval rejected: {reason}")

        return _gw.process_payment(
            payer_vpa=req.payer_vpa,
            payee_vpa=req.payee_vpa,
            amount_paise=req.amount_paise,
            note=req.note,
            txn_id=req.txn_id,
        )

    @app.get("/balance/{vpa}", response_model=BalanceResponse)
    def balance(vpa: str) -> BalanceResponse:
        return BalanceResponse(vpa=vpa, balance_paise=_gw.get_balance(vpa))

    return app
