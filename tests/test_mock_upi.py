"""Tests for the mock UPI gateway (Migrated to signed tickets for Phase 3)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kavach.issuer import generate_keypair, sign_approval_ticket
from kavach.mock_upi import MockUPIGateway, create_app


@pytest.fixture
def keys():
    priv, pub = generate_keypair()
    return priv, pub


@pytest.fixture
def gateway(keys) -> MockUPIGateway:
    priv, pub = keys
    gw = MockUPIGateway(interceptor_public_key=pub)
    gw.set_balance("user@upi", 1000000)  # ₹10,000
    gw.set_balance("ondc-grocer@upi", 500000)
    gw.set_balance("attacker_wallet@upi", 0)
    return gw


@pytest.fixture
def client(gateway) -> TestClient:
    app = create_app(gateway)
    return TestClient(app)


class TestMockUPIGateway:
    def test_pay_success(self, client, keys):
        priv, pub = keys
        ticket = sign_approval_ticket(
            interceptor_private_key=priv,
            mandate_jti="mandate-upi-1",
            payee_vpa="ondc-grocer@upi",
            amount_paise=100000,
            nonce="nonce-upi-1",
        )
        resp = client.post(
            "/pay",
            json={
                "payer_vpa": "user@upi",
                "payee_vpa": "ondc-grocer@upi",
                "amount_paise": 100000,
                "note": "groceries",
                "txn_id": "txn-001",
            },
            headers={"x-kavach-approval-ticket": ticket},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["payer_balance_paise"] == 900000
        assert data["payee_balance_paise"] == 600000

    def test_pay_without_approval_header(self, client):
        resp = client.post(
            "/pay",
            json={
                "payer_vpa": "user@upi",
                "payee_vpa": "ondc-grocer@upi",
                "amount_paise": 50000,
            },
        )
        assert resp.status_code == 403

    def test_pay_wrong_approval_header(self, client):
        resp = client.post(
            "/pay",
            json={
                "payer_vpa": "user@upi",
                "payee_vpa": "ondc-grocer@upi",
                "amount_paise": 50000,
            },
            headers={"x-kavach-approval-ticket": "invalid.jwt.token"},
        )
        assert resp.status_code == 403

    def test_pay_insufficient_balance(self, client, keys):
        priv, pub = keys
        ticket = sign_approval_ticket(
            interceptor_private_key=priv,
            mandate_jti="mandate-upi-2",
            payee_vpa="ondc-grocer@upi",
            amount_paise=9999999,
            nonce="nonce-upi-2",
        )
        resp = client.post(
            "/pay",
            json={
                "payer_vpa": "user@upi",
                "payee_vpa": "ondc-grocer@upi",
                "amount_paise": 9999999,
            },
            headers={"x-kavach-approval-ticket": ticket},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "Insufficient" in data["message"]

    def test_balance_endpoint(self, client):
        resp = client.get("/balance/user@upi")
        assert resp.status_code == 200
        data = resp.json()
        assert data["balance_paise"] == 1000000

    def test_balance_unknown_vpa(self, client):
        resp = client.get("/balance/unknown@upi")
        assert resp.status_code == 200
        assert resp.json()["balance_paise"] == 0

    def test_gateway_without_keys_refuses_pay(self):
        """Gateway initialized without public key must fail closed."""
        gw_unconfigured = MockUPIGateway(interceptor_public_key=None)
        app = create_app(gw_unconfigured)
        raw_client = TestClient(app)
        resp = raw_client.post(
            "/pay",
            json={
                "payer_vpa": "user@upi",
                "payee_vpa": "ondc-grocer@upi",
                "amount_paise": 10000,
            },
            headers={"x-kavach-approval-ticket": "some-ticket"},
        )
        assert resp.status_code == 403
        assert "fail-closed" in resp.json()["detail"]
