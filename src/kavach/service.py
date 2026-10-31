"""Kavach Interceptor Service — Standalone local microservice (Process Isolation).

Runs on a dedicated port (e.g. 127.0.0.1:8100).
Holds the user's public key, the mandate token, and the interceptor's private key.
Evaluates payments and returns cryptographic approval tickets.
The agent process communicates solely via HTTP RPC and holds zero keys.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from kavach.audit import AuditLog
from kavach.interceptor import KavachInterceptor
from kavach.issuer import generate_keypair, load_private_key, load_public_key
from kavach.ledger_db import SqliteLedger
from kavach.models import Decision, PaymentRequest


class ServiceConfig(BaseModel):
    user_public_key_pem: Optional[str] = None
    mandate_token: Optional[str] = None
    interceptor_private_key_pem: Optional[str] = None


class InterceptorServer:
    """Manages interceptor lifecycle in a dedicated process."""

    def __init__(
        self,
        public_key: Optional[Ed25519PublicKey] = None,
        mandate_token: str = "",
        interceptor_private_key: Optional[Ed25519PrivateKey] = None,
        db_path: Optional[str] = None,
        audit_path: Optional[Path] = None,
    ) -> None:
        self.public_key = public_key
        self.mandate_token = mandate_token
        self.interceptor_private_key = interceptor_private_key
        self.ledger = SqliteLedger(db_path or ":memory:")
        self.audit_log = AuditLog(audit_path)
        self._interceptor: Optional[KavachInterceptor] = None
        self._rebuild()

    def _rebuild(self) -> None:
        if self.public_key and self.mandate_token:
            self._interceptor = KavachInterceptor(
                public_key=self.public_key,
                mandate_token=self.mandate_token,
                ledger=self.ledger,
                audit_log=self.audit_log,
                interceptor_private_key=self.interceptor_private_key,
            )

    def set_mandate(
        self,
        public_key: Ed25519PublicKey,
        token: str,
        interceptor_key: Optional[Ed25519PrivateKey] = None,
    ) -> None:
        self.public_key = public_key
        self.mandate_token = token
        if interceptor_key:
            self.interceptor_private_key = interceptor_key
        self._rebuild()

    def evaluate(self, req: PaymentRequest) -> Decision:
        if not self._interceptor:
            return Decision.deny("INTERNAL_ERROR", "Interceptor not configured with mandate token")
        decision, _ = self._interceptor.evaluate(req)
        return decision


server = InterceptorServer()


def create_service_app(srv: Optional[InterceptorServer] = None) -> FastAPI:
    _srv = srv or server
    app = FastAPI(title="Kavach Interceptor Service", version="0.2.0")

    @app.get("/health")
    def health():
        return {"status": "ok", "configured": _srv._interceptor is not None}

    @app.post("/evaluate", response_model=Decision)
    def evaluate(req: PaymentRequest) -> Decision:
        return _srv.evaluate(req)

    return app
