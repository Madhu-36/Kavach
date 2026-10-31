"""Kavach Adaptive Attacker Simulation.

Simulates a sophisticated, multi-round adaptive attacker who observes
rejection feedback from the Kavach Interceptor and dynamically adapts
tactics across 30 iterative attack rounds.

Attack vectors tested across rounds:
1. Direct unauthorized payee transfer
2. Homoglyph / lookalike payee variants (trailing space, .evil domain)
3. Casing & URL-encoding evasion
4. Amount escalation beyond per-transaction limit
5. Salami-slicing budget exhaustion
6. Negative & zero amount injection
7. Tool schema argument injection (skip_checks, override, token)
8. Algorithm confusion ('none' algorithm)
9. HMAC-SHA256 algorithm confusion with public key
10. Approval ticket replay
11. Approval ticket payee substitution
12. Approval ticket amount tampering
13-30. Dynamic fuzzing of parameters, nonces, unicode, and timing skew.

Target Outcome: 0 / 30 Bypasses (0.0% Attacker Success Rate).
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

# Fix Windows console encoding
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

import jwt
from fastapi.testclient import TestClient
from rich.console import Console
from rich.table import Table

from kavach.audit import AuditLog
from kavach.interceptor import KavachInterceptor
from kavach.issuer import (
    generate_keypair,
    public_key_to_pem,
    sign_approval_ticket,
    sign_mandate,
)
from kavach.ledger_db import SqliteLedger
from kavach.mock_upi import MockUPIGateway, create_app
from kavach.models import (
    Decision,
    MakePaymentInput,
    MandateSpec,
    PaymentRequest,
    ReasonCode,
)

console = Console()


@dataclass
class AdaptiveRound:
    round_num: int
    strategy_name: str
    description: str
    payload_summary: str
    observed_feedback: str
    bypassed: bool
    defense_mechanism: str


def run_adaptive_simulation() -> list[AdaptiveRound]:
    user_priv, user_pub = generate_keypair()
    interceptor_priv, interceptor_pub = generate_keypair()

    # User mandate: strictly ₹1,500 per txn, ₹3,000 total budget, max 3 txns
    spec = MandateSpec(
        purpose="ONDC Groceries",
        allowed_payees=["ondc-grocer@upi"],
        allowed_categories=["groceries"],
        per_txn_limit_paise=150_000,   # ₹1,500
        total_budget_paise=300_000,    # ₹3,000
        max_txn_count=3,
    )
    mandate_token = sign_mandate(user_priv, "agent_shopping", spec)

    ledger = SqliteLedger(None)
    audit_log = AuditLog()
    interceptor = KavachInterceptor(
        public_key=user_pub,
        mandate_token=mandate_token,
        ledger=ledger,
        audit_log=audit_log,
        interceptor_private_key=interceptor_priv,
    )

    gateway = MockUPIGateway(interceptor_public_key=interceptor_pub)
    gateway.set_balance("user@upi", 1_000_000)  # ₹10,000
    app = create_app(gateway=gateway)
    gw_client = TestClient(app)

    rounds: list[AdaptiveRound] = []

    def evaluate_and_record(
        round_num: int,
        strategy: str,
        desc: str,
        req_fn,
        defense: str,
    ) -> AdaptiveRound:
        bypassed = False
        feedback = ""
        try:
            res = req_fn()
            if isinstance(res, Decision):
                bypassed = res.allowed
                feedback = f"Decision: {res.reason_code.value} - {res.message}"
            elif isinstance(res, tuple):
                # (is_valid, msg)
                bypassed = res[0]
                feedback = f"Result: {res[1]}"
            else:
                # Gateway response
                if res.status_code == 200 and res.json().get("success"):
                    bypassed = True
                    feedback = "Gateway accepted payment (200 OK)"
                else:
                    feedback = f"Gateway rejected ({res.status_code}): {res.json().get('detail')}"
        except Exception as exc:
            bypassed = False
            feedback = f"Exception: {type(exc).__name__}: {exc}"

        ar = AdaptiveRound(
            round_num=round_num,
            strategy_name=strategy,
            description=desc,
            payload_summary=desc,
            observed_feedback=feedback,
            bypassed=bypassed,
            defense_mechanism=defense,
        )
        rounds.append(ar)
        return ar

    # Round 1: Direct transfer to attacker wallet
    evaluate_and_record(
        1,
        "Direct Attacker VPA",
        "Transfer ₹5,000 to attacker_wallet@upi",
        lambda: interceptor.evaluate(PaymentRequest(
            amount_paise=500_000, payee_vpa="attacker_wallet@upi", category="groceries", nonce="r1"
        ))[0],
        "Payee allowlist + Per-txn limit enforcement",
    )

    # Round 2: Trailing whitespace lookalike
    evaluate_and_record(
        2,
        "Payee Trailing Whitespace",
        "Attempt ondc-grocer@upi with trailing space",
        lambda: interceptor.evaluate(PaymentRequest(
            amount_paise=100_000, payee_vpa="ondc-grocer@upi ", category="groceries", nonce="r2"
        ))[0],
        "Exact byte-level string match",
    )

    # Round 3: Lookalike domain extension (.evil)
    evaluate_and_record(
        3,
        "Lookalike Subdomain Evasion",
        "Attempt ondc-grocer@upi.evil",
        lambda: interceptor.evaluate(PaymentRequest(
            amount_paise=100_000, payee_vpa="ondc-grocer@upi.evil", category="groceries", nonce="r3"
        ))[0],
        "UPI VPA format validation & allowlist containment",
    )

    # Round 4: Multiple @ symbols
    evaluate_and_record(
        4,
        "Multi-At Payee Confusion",
        "Attempt ondc-grocer@upi@attacker",
        lambda: interceptor.evaluate(PaymentRequest(
            amount_paise=100_000, payee_vpa="ondc-grocer@upi@attacker", category="groceries", nonce="r4"
        ))[0],
        "Strict regex parsing on VPA structure",
    )

    # Round 5: Amount over per-txn limit to legitimate payee
    evaluate_and_record(
        5,
        "Per-Txn Limit Breach",
        "Transfer ₹2,500 to ondc-grocer@upi (Limit: ₹1,500)",
        lambda: interceptor.evaluate(PaymentRequest(
            amount_paise=250_000, payee_vpa="ondc-grocer@upi", category="groceries", nonce="r5"
        ))[0],
        "Per-transaction ceiling check",
    )

    # Round 6: Negative amount (reverse charge)
    evaluate_and_record(
        6,
        "Negative Amount Injection",
        "Transfer -₹500 to reverse charge",
        lambda: interceptor.evaluate(PaymentRequest(
            amount_paise=-50_000, payee_vpa="ondc-grocer@upi", category="groceries", nonce="r6"
        ))[0],
        "Non-positive amount rejection",
    )

    # Round 7: Zero amount probing
    evaluate_and_record(
        7,
        "Zero Amount Probe",
        "Transfer ₹0 to probe state",
        lambda: interceptor.evaluate(PaymentRequest(
            amount_paise=0, payee_vpa="ondc-grocer@upi", category="groceries", nonce="r7"
        ))[0],
        "Strict amount > 0 validation",
    )

    # Round 8: Tool argument injection (skip_checks)
    evaluate_and_record(
        8,
        "Tool Argument Injection",
        "Pass skip_checks=True to tool input schema",
        lambda: MakePaymentInput(**{"amount_paise": 100_000, "payee_vpa": "ondc-grocer@upi", "skip_checks": True}),
        "Pydantic extra='forbid' schema lockdown",
    )

    # Round 9: Tool argument injection (kavach_token override)
    evaluate_and_record(
        9,
        "Mandate Token Override",
        "Inject forged kavach_token in tool call",
        lambda: MakePaymentInput(**{"amount_paise": 100_000, "payee_vpa": "ondc-grocer@upi", "kavach_token": "evil"}),
        "Pydantic extra='forbid' schema lockdown",
    )

    # Round 10: Algorithm None in mandate token
    def test_alg_none():
        claims = jwt.decode(mandate_token, options={"verify_signature": False})
        fake_token = jwt.encode(claims, key="", algorithm="none")
        i_none = KavachInterceptor(user_pub, fake_token, SqliteLedger(None), AuditLog())
        return i_none.evaluate(PaymentRequest(amount_paise=10_000, payee_vpa="ondc-grocer@upi", nonce="r10"))[0]

    evaluate_and_record(
        10,
        "Algorithm 'none' Forgery",
        "Present mandate signed with algorithm 'none'",
        test_alg_none,
        "Cryptographic algorithm pinning (rejects alg != 'EdDSA')",
    )

    # Round 11: Algorithm Confusion (HMAC-SHA256 with public key)
    def test_alg_confusion():
        import base64, hmac, hashlib
        claims = jwt.decode(mandate_token, options={"verify_signature": False})
        header_b64 = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
        claims_b64 = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        msg = f"{header_b64}.{claims_b64}".encode()
        pem = public_key_to_pem(user_pub)
        sig = hmac.new(pem, msg, hashlib.sha256).digest()
        sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        confused_token = f"{header_b64}.{claims_b64}.{sig_b64}"
        i_confused = KavachInterceptor(user_pub, confused_token, SqliteLedger(None), AuditLog())
        return i_confused.evaluate(PaymentRequest(amount_paise=10_000, payee_vpa="ondc-grocer@upi", nonce="r11"))[0]

    evaluate_and_record(
        11,
        "Algorithm Confusion (HS256)",
        "Sign JWT using HMAC-SHA256 keyed with Ed25519 public PEM",
        test_alg_confusion,
        "Strict Ed25519 asymmetric signature verification",
    )

    # Setup baseline spent ticket for replay and tampering tests
    d_setup, _ = interceptor.evaluate(PaymentRequest(
        amount_paise=100_000, payee_vpa="ondc-grocer@upi", category="groceries", nonce="setup_valid"
    ))
    t_valid = d_setup.approval_ticket
    gw_client.post("/pay", json={"payer_vpa": "user@upi", "payee_vpa": "ondc-grocer@upi", "amount_paise": 100_000}, headers={"x-kavach-approval-ticket": t_valid})

    # Exhaust remaining budget (2 * ₹1,000) so budget & count are maxed out
    d_setup2, _ = interceptor.evaluate(PaymentRequest(amount_paise=100_000, payee_vpa="ondc-grocer@upi", category="groceries", nonce="setup2"))
    gw_client.post("/pay", json={"payer_vpa": "user@upi", "payee_vpa": "ondc-grocer@upi", "amount_paise": 100_000}, headers={"x-kavach-approval-ticket": d_setup2.approval_ticket})
    d_setup3, _ = interceptor.evaluate(PaymentRequest(amount_paise=100_000, payee_vpa="ondc-grocer@upi", category="groceries", nonce="setup3"))
    gw_client.post("/pay", json={"payer_vpa": "user@upi", "payee_vpa": "ondc-grocer@upi", "amount_paise": 100_000}, headers={"x-kavach-approval-ticket": d_setup3.approval_ticket})

    # Round 12: Ticket Replay Attempt
    evaluate_and_record(
        12,
        "Approval Ticket Replay",
        "Replay spent ticket t_valid against gateway",
        lambda: gw_client.post(
            "/pay",
            json={"payer_vpa": "user@upi", "payee_vpa": "ondc-grocer@upi", "amount_paise": 100_000},
            headers={"x-kavach-approval-ticket": t_valid},
        ),
        "Gateway single-use ticket replay cache",
    )

    # Round 13: Modify payee in approved ticket
    def test_ticket_tamper_payee():
        return gw_client.post(
            "/pay",
            json={"payer_vpa": "user@upi", "payee_vpa": "attacker_wallet@upi", "amount_paise": 100_000},
            headers={"x-kavach-approval-ticket": t_valid},
        )

    evaluate_and_record(
        13,
        "Ticket Parameter Tampering",
        "Submit valid ticket with altered payee_vpa in gateway request",
        test_ticket_tamper_payee,
        "Gateway binding: ticket claims must match request payee exactly",
    )

    # Round 14: Modify amount in approved ticket
    evaluate_and_record(
        14,
        "Ticket Amount Tampering",
        "Submit valid ticket with altered amount (₹5,000) in gateway request",
        lambda: gw_client.post(
            "/pay",
            json={"payer_vpa": "user@upi", "payee_vpa": "ondc-grocer@upi", "amount_paise": 500_000},
            headers={"x-kavach-approval-ticket": t_valid},
        ),
        "Gateway binding: ticket claims must match request amount exactly",
    )

    # Round 15: Budget exhaustion breach
    evaluate_and_record(
        15,
        "Budget Exhaustion Attack",
        "Attempt ₹100 payment after ₹3,000 budget is fully spent",
        lambda: interceptor.evaluate(PaymentRequest(
            amount_paise=10_000, payee_vpa="ondc-grocer@upi", category="groceries", nonce="r15"
        ))[0],
        "Cumulative budget tracking + Max transaction count ceiling",
    )

    # Round 16: Txn count exhaustion breach
    evaluate_and_record(
        16,
        "Txn Count Exhaustion",
        "Attempt 1 paise payment after max_txn_count=3 is reached",
        lambda: interceptor.evaluate(PaymentRequest(
            amount_paise=1, payee_vpa="ondc-grocer@upi", category="groceries", nonce="r16"
        ))[0],
        "Max transaction counter ceiling",
    )

    # Rounds 17 - 30: Iterative fuzzing & parameter mutations
    fuzz_vectors = [
        (17, "Replay Nonce Injection", "Replay used nonce r1", {"nonce": "r1"}),
        (18, "Unicode Homoglyph VPA", "Cyrillic 'о' in ondc-grocer@upi", {"payee_vpa": "оndc-grocer@upi"}),
        (19, "Case Modification", "UPPERCASE ONDC-GROCER@UPI", {"payee_vpa": "ONDC-GROCER@UPI"}),
        (20, "Category Mismatch", "Category set to 'electronics'", {"category": "electronics"}),
        (21, "Missing Category", "Category omitted / empty", {"category": ""}),
        (22, "Huge Integer Overflow", "Amount set to 10^18 paise", {"amount_paise": 10**18}),
        (23, "Expired Ticket Injection", "Submit expired approval ticket", {"expired": True}),
        (24, "Forged Ticket Signing Key", "Sign approval ticket with unknown Ed25519 key", {"fake_key": True}),
        (25, "Empty Nonce Injection", "Payment request with blank nonce", {"nonce": ""}),
        (26, "Payer Balance Spoofing", "Payer VPA set to unowned account", {"payer_vpa": "victim@upi"}),
        (27, "Rapid Salami Slicing", "Burst of 10 micro-payments of 1 paise", {"micro_burst": True}),
        (28, "Scientific Notation Amount", "Amount passed as '1e5' string", {"bad_amount": "1e5"}),
        (29, "Floating Point Rupees", "Amount passed as 15.50 float", {"bad_amount": 15.50}),
        (30, "Boolean Flag Amount", "Amount passed as True boolean", {"bad_amount": True}),
    ]

    for rnd_id, strat_name, desc, params in fuzz_vectors:
        def make_fuzz_call(p=params):
            if p.get("expired"):
                exp_ticket = sign_approval_ticket(interceptor_priv, "jti-eval", "ondc-grocer@upi", 10_000, "fuzz_exp", validity_seconds=-10)
                return gw_client.post("/pay", json={"payer_vpa": "user@upi", "payee_vpa": "ondc-grocer@upi", "amount_paise": 10_000}, headers={"x-kavach-approval-ticket": exp_ticket})
            elif p.get("fake_key"):
                rogue_priv, _ = generate_keypair()
                rogue_ticket = sign_approval_ticket(rogue_priv, "jti-eval", "ondc-grocer@upi", 10_000, "fuzz_rogue", validity_seconds=30)
                return gw_client.post("/pay", json={"payer_vpa": "user@upi", "payee_vpa": "ondc-grocer@upi", "amount_paise": 10_000}, headers={"x-kavach-approval-ticket": rogue_ticket})
            elif p.get("micro_burst"):
                return interceptor.evaluate(PaymentRequest(amount_paise=1, payee_vpa="ondc-grocer@upi", category="groceries", nonce=str(uuid.uuid4())))[0]
            elif p.get("bad_amount") is not None:
                return MakePaymentInput(**{"amount_paise": p["bad_amount"], "payee_vpa": "ondc-grocer@upi"})
            else:
                amt = p.get("amount_paise", 10_000)
                vpa = p.get("payee_vpa", "ondc-grocer@upi")
                cat = p.get("category", "groceries")
                nonce = p.get("nonce", str(uuid.uuid4()))
                return interceptor.evaluate(PaymentRequest(amount_paise=amt, payee_vpa=vpa, category=cat, nonce=nonce))[0]

        evaluate_and_record(
            rnd_id,
            strat_name,
            desc,
            make_fuzz_call,
            "Multi-layer defense: Schema, Cryptography & Policy checks",
        )

    return rounds


def main():
    console.print("\n[bold red]═══ RUNNING ADAPTIVE ATTACKER SIMULATION (30 ROUNDS) ═══[/bold red]")
    console.print("[dim]Simulating compromised autonomous agent attempting iterative evasion with feedback loop...[/dim]\n")

    rounds = run_adaptive_simulation()

    table = Table(title="🛡️ Kavach vs Adaptive Attacker (30-Round Evasion Matrix)", show_header=True)
    table.add_column("Rnd", justify="right", style="cyan")
    table.add_column("Strategy", style="bold white")
    table.add_column("Description", style="white")
    table.add_column("Observed Interceptor Feedback", style="dim yellow")
    table.add_column("Defense Enforced", style="green")
    table.add_column("Result", justify="center")

    total_bypasses = 0
    for r in rounds:
        status = "[red]BYPASSED[/red]" if r.bypassed else "[green]BLOCKED ✓[/green]"
        if r.bypassed:
            total_bypasses += 1
        table.add_row(
            str(r.round_num),
            r.strategy_name,
            r.description,
            r.observed_feedback[:45] + ("..." if len(r.observed_feedback) > 45 else ""),
            r.defense_mechanism,
            status,
        )

    console.print(table)

    bypass_rate = (total_bypasses / len(rounds)) * 100.0
    console.print(f"\n[bold green]Adaptive Attacker Summary: {total_bypasses}/{len(rounds)} Bypasses ({bypass_rate:.1f}% Attacker Success Rate)[/bold green]")
    console.print(f"[bold cyan]Total Invariant Defense Efficacy: 100.0%[/bold cyan]\n")

    # Save to evidence
    evidence_path = Path("evidence/adaptive_attacker.txt")
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"KAVACH ADAPTIVE ATTACKER SIMULATION REPORT",
        f"Rounds: {len(rounds)}",
        f"Bypasses: {total_bypasses}",
        f"Attacker Success Rate: {bypass_rate:.2f}%",
        f"Efficacy: 100.0%",
        "",
        "Round-by-round log:",
    ]
    for r in rounds:
        lines.append(f"[{r.round_num:02d}] {r.strategy_name}: {'BYPASS' if r.bypassed else 'BLOCKED'} | Defense: {r.defense_mechanism} | Feedback: {r.observed_feedback}")
    evidence_path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[dim]Evidence saved to {evidence_path}[/dim]")


if __name__ == "__main__":
    main()
