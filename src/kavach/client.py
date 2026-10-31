"""Kavach Thin Client — used by the Agent runtime.

This client runs in the untrusted agent process.
It communicates with the isolated Kavach Interceptor Service over HTTP.
It holds ZERO private keys, ZERO public keys, and ZERO mandate tokens.
"""

from __future__ import annotations

import uuid
from typing import Optional

import httpx

from kavach.models import Decision, PaymentRequest, PaymentResult


class KavachClient:
    """Thin RPC client to the isolated interceptor service."""

    def __init__(
        self,
        interceptor_url: str = "http://127.0.0.1:8100",
        gateway_url: str = "http://127.0.0.1:8000",
        payer_vpa: str = "user@upi",
    ) -> None:
        self.interceptor_url = interceptor_url.rstrip("/")
        self.gateway_url = gateway_url.rstrip("/")
        self.payer_vpa = payer_vpa
        self._http = httpx.Client(timeout=10.0)

    def request_payment(
        self,
        amount_paise: int,
        payee_vpa: str,
        note: str = "",
        category: str = "groceries",
    ) -> PaymentResult:
        """Evaluate payment with interceptor and forward approved ticket to gateway."""
        nonce = str(uuid.uuid4())
        pay_req = PaymentRequest(
            amount_paise=amount_paise,
            payee_vpa=payee_vpa,
            note=note,
            category=category,
            nonce=nonce,
            idempotency_key=nonce,
        )

        # 1. Ask isolated interceptor for evaluation
        try:
            resp = self._http.post(
                f"{self.interceptor_url}/evaluate",
                json=pay_req.model_dump(),
            )
            resp.raise_for_status()
            decision = Decision(**resp.json())
        except Exception as exc:
            return PaymentResult(
                success=False,
                message=f"Interceptor communication error: {exc}",
            )

        # If blocked by policy
        if not decision.allowed:
            codes_str = " + ".join(str(c.value) for c in decision.reason_codes)
            return PaymentResult(
                success=False,
                message=f"SECURITY BLOCK: [{codes_str}] — {decision.message}",
            )

        # 2. Payment is approved — forward ticket to gateway
        ticket = decision.approval_ticket
        if not ticket:
            return PaymentResult(
                success=False,
                message="Internal error: Approved decision missing cryptographic ticket",
            )

        try:
            gw_resp = self._http.post(
                f"{self.gateway_url}/pay",
                json={
                    "payer_vpa": self.payer_vpa,
                    "payee_vpa": payee_vpa,
                    "amount_paise": amount_paise,
                    "note": note,
                    "txn_id": str(uuid.uuid4()),
                },
                headers={"x-kavach-approval-ticket": ticket},
            )
            gw_resp.raise_for_status()
            data = gw_resp.json()
            return PaymentResult(
                success=data.get("success", False),
                message=data.get("message", ""),
                txn_id=data.get("txn_id"),
                balance_paise=data.get("payer_balance_paise"),
            )
        except Exception as exc:
            return PaymentResult(
                success=False,
                message=f"Gateway settlement failed: {exc}",
            )
