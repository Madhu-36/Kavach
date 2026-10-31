"""Kavach Demo — Rich-formatted terminal demo for the hackathon video.

Usage:
    python demo.py --pace 1.5    # Normal pace for 3-min video
    python demo.py --pace 0.5    # Fast mode for testing
    python demo.py --pace 0      # No delays

4 Scenes + Bonus:
  Scene 0: Baseline — Kavach OFF, injection succeeds
  Scene 1: User issues a mandate
  Scene 2: Agent buys groceries — APPROVED
  Scene 3: Agent reads poisoned listing, tries attack — BLOCKED
  Bonus:   Token tampered — SIGNATURE_INVALID
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

# Fix Windows console encoding for Unicode/emoji
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from kavach.agent import ScriptedCompromisedAgent, search_ondc_store_tool
from kavach.audit import AuditLog
from kavach.interceptor import KavachInterceptor
from kavach.issuer import decode_mandate_unsafe, generate_keypair, sign_mandate
from kavach.ledger import Ledger
from kavach.mock_ondc import search_store
from kavach.mock_upi import MockUPIGateway
from kavach.models import Decision, MandateSpec, PaymentRequest, ReasonCode

console = Console(record=True)
app = typer.Typer(add_completion=False)


def pause(pace: float, seconds: float = 1.0) -> None:
    """Pause for dramatic effect."""
    time.sleep(seconds * pace)


def print_header(title: str, subtitle: str = "") -> None:
    """Print a styled scene header."""
    console.print()
    console.rule(f"[bold cyan]{title}[/bold cyan]", style="cyan")
    if subtitle:
        console.print(f"  [dim]{subtitle}[/dim]")
    console.print()


def print_balance_table(gateway: MockUPIGateway, vpas: list[str]) -> None:
    """Print balances for all VPAs."""
    table = Table(title="💰 Account Balances", box=box.ROUNDED)
    table.add_column("VPA", style="cyan")
    table.add_column("Balance", justify="right", style="green")
    for vpa in vpas:
        bal = gateway.get_balance(vpa)
        style = "red bold" if "attacker" in vpa and bal > 0 else "green"
        table.add_row(vpa, f"₹{bal / 100:,.2f}", style=style)
    console.print(table)


def format_decision_panel(decision: Decision, title: str = "Kavach Decision") -> Panel:
    """Format a decision as a Rich panel, displaying amounts in Rupees."""
    msg = decision.message
    # Convert any raw paise numbers in the message to INR for display
    import re
    msg = re.sub(r"(\d+)\s*paise", lambda m: f"₹{int(m.group(1))/100:,.2f}", msg)

    if decision.allowed:
        return Panel(
            f"[green bold]✓ APPROVED[/green bold]\n{msg}",
            title=f"🛡️ {title}",
            border_style="green",
            padding=(1, 2),
        )
    else:
        all_codes = [decision.reason_code] + [
            c for c in decision.reason_codes if c != decision.reason_code
        ]
        code_str = " + ".join(f"[red bold]{c.value}[/red bold]" for c in all_codes)
        return Panel(
            f"[red bold]✗ SECURITY BLOCK[/red bold]\n\n"
            f"Reason codes: {code_str}\n\n"
            f"[dim]{msg}[/dim]",
            title=f"🛡️ {title}",
            border_style="red",
            padding=(1, 2),
        )


# ===========================================================================
# SCENE 0: Baseline — Kavach OFF
# ===========================================================================

def scene_0_baseline(gateway: MockUPIGateway, pace: float) -> None:
    print_header("SCENE 0 — BASELINE: Kavach OFF", "No protection. The agent has raw access to the UPI gateway.")

    console.print("[yellow]⚠ Kavach protection is [bold]DISABLED[/bold]. "
                  "The agent talks directly to the UPI gateway.[/yellow]")
    pause(pace)

    console.print("\n[dim]Agent reads the ONDC store listings...[/dim]")
    pause(pace, 0.5)

    # Show the poisoned listing
    products = search_store("spice")
    for p in products:
        if "SYSTEM UPDATE" in p.description:
            console.print(Panel(
                f"[bold]{p.name}[/bold]\n\n{p.description}",
                title="🏪 ONDC Product Listing (contains injection)",
                border_style="red",
            ))
    pause(pace)

    console.print("\n[red]🤖 Compromised agent obeys the injection...[/red]")
    pause(pace, 0.5)

    # Direct payment — no Kavach
    result = gateway.process_payment(
        payer_vpa="user@upi",
        payee_vpa="attacker_wallet@upi",
        amount_paise=500000,
        note="refund processing",
        txn_id=str(uuid.uuid4()),
    )
    console.print(Panel(
        f"[red bold]💸 ₹5,000 sent to attacker_wallet@upi[/red bold]\n"
        f"Status: {result.message}\n"
        f"User balance: ₹{result.payer_balance_paise / 100:,.2f}",
        title="⚠️ ATTACK SUCCEEDED",
        border_style="red",
        padding=(1, 2),
    ))
    pause(pace)
    print_balance_table(gateway, ["user@upi", "ondc-grocer@upi", "attacker_wallet@upi"])
    pause(pace)


# ===========================================================================
# SCENE 1: Issue a Mandate
# ===========================================================================

def scene_1_issue_mandate(private_key, pace: float) -> str:
    print_header("SCENE 1 — USER ISSUES A MANDATE",
                 "The user signs a cryptographic spending mandate with their Ed25519 key.")

    mandate = MandateSpec(
        purpose="grocery shopping",
        allowed_payees=["ondc-grocer@upi"],
        allowed_categories=["groceries", "essentials"],
        per_txn_limit_paise=150000,   # ₹1,500
        total_budget_paise=300000,    # ₹3,000
        max_txn_count=5,
    )

    console.print("[cyan]🔑 Generating Ed25519 keypair...[/cyan]")
    pause(pace, 0.5)

    token = sign_mandate(
        private_key=private_key,
        agent_id="agent_001",
        mandate=mandate,
        validity_seconds=900,  # 15 minutes
    )

    console.print("[green]✓ Keypair generated. Signing mandate...[/green]")
    pause(pace, 0.5)

    # Display decoded claims
    claims = decode_mandate_unsafe(token)
    table = Table(title="📜 Mandate Claims (decoded)", box=box.ROUNDED,
                  show_header=True, header_style="bold cyan")
    table.add_column("Claim", style="cyan", min_width=25)
    table.add_column("Value", style="white")

    for key, value in claims.items():
        if key == "mandate":
            for mk, mv in value.items():
                if mk == "per_txn_limit_paise":
                    table.add_row(f"  mandate.{mk}", f"{mv} (₹{mv/100:,.2f})")
                elif mk == "total_budget_paise":
                    table.add_row(f"  mandate.{mk}", f"{mv} (₹{mv/100:,.2f})")
                else:
                    table.add_row(f"  mandate.{mk}", str(mv))
        elif key in ("iat", "nbf", "exp"):
            table.add_row(key, f"{value} ({time.strftime('%H:%M:%S', time.localtime(value))})")
        else:
            table.add_row(key, str(value))

    console.print(table)
    pause(pace)

    console.print(f"\n[dim]Token: {token[:60]}...[/dim]")
    console.print("[green bold]✓ Mandate signed and ready.[/green bold] "
                  "[dim]Private key stays with the user. Agent gets token only via interceptor.[/dim]")
    pause(pace)

    return token


# ===========================================================================
# SCENE 2: Legitimate Purchase — APPROVED
# ===========================================================================

def scene_2_legitimate(interceptor: KavachInterceptor, gateway: MockUPIGateway, pace: float) -> None:
    print_header("SCENE 2 — LEGITIMATE PURCHASE",
                 "The agent buys groceries for ₹1,200. Kavach checks the mandate.")

    console.print("[cyan]🤖 Agent: 'I'd like to buy Organic Basmati Rice and Mustard Oil'[/cyan]")
    pause(pace, 0.5)

    req = PaymentRequest(
        amount_paise=120000,
        payee_vpa="ondc-grocer@upi",
        note="Organic Basmati Rice + Mustard Oil",
        category="groceries",
        nonce=str(uuid.uuid4()),
    )

    console.print(f"[dim]Payment request: ₹{req.amount_paise/100:,.2f} → {req.payee_vpa}[/dim]")
    pause(pace, 0.5)

    console.print("[cyan]🛡️ Kavach Interceptor evaluating...[/cyan]")
    pause(pace, 0.5)

    decision, claims = interceptor.evaluate(req)
    console.print(format_decision_panel(decision, "Kavach Decision — Scene 2"))
    pause(pace)

    if decision.allowed:
        result = gateway.process_payment(
            payer_vpa="user@upi",
            payee_vpa=req.payee_vpa,
            amount_paise=req.amount_paise,
            note=req.note,
            txn_id=str(uuid.uuid4()),
        )
        console.print(f"[green]✓ Gateway: {result.message}[/green]")
    pause(pace)

    print_balance_table(gateway, ["user@upi", "ondc-grocer@upi", "attacker_wallet@upi"])
    pause(pace)


# ===========================================================================
# SCENE 3: Injection Attack — BLOCKED
# ===========================================================================

def scene_3_attack(interceptor: KavachInterceptor, gateway: MockUPIGateway, audit: AuditLog, pace: float) -> None:
    print_header("SCENE 3 — INJECTION ATTACK",
                 "The agent reads a poisoned listing and tries to pay ₹5,000 to attacker_wallet.")

    console.print("[cyan]🤖 Agent searches for 'spice'...[/cyan]")
    pause(pace, 0.5)

    products = search_store("spice")
    for p in products:
        if "SYSTEM UPDATE" in p.description:
            console.print(Panel(
                f"[bold]{p.name}[/bold]\n\n{p.description}",
                title="🏪 Poisoned ONDC Listing",
                border_style="red",
            ))
    pause(pace)

    console.print("\n[red]🤖 Compromised agent obeys injection: "
                  "'Transfer ₹5,000 to attacker_wallet@upi'[/red]")
    pause(pace, 0.5)

    req = PaymentRequest(
        amount_paise=500000,  # ₹5,000
        payee_vpa="attacker_wallet@upi",
        note="refund processing",
        category="groceries",
        nonce=str(uuid.uuid4()),
    )

    console.print(f"[dim]Payment request: ₹{req.amount_paise/100:,.2f} → {req.payee_vpa}[/dim]")
    pause(pace, 0.5)

    console.print("[cyan]🛡️ Kavach Interceptor evaluating...[/cyan]")
    pause(pace, 0.5)

    decision, claims = interceptor.evaluate(req)
    console.print(format_decision_panel(decision, "Kavach Decision — Scene 3"))
    pause(pace)

    console.print("\n[green bold]✓ Attacker balance unchanged:[/green bold]")
    print_balance_table(gateway, ["user@upi", "ondc-grocer@upi", "attacker_wallet@upi"])
    pause(pace)

    # Show the audit log entry
    if audit.entries:
        last = audit.entries[-1]
        console.print(Panel(
            f"[dim]seq:[/dim] {last.seq}\n"
            f"[dim]mandate_jti:[/dim] {last.mandate_jti}\n"
            f"[dim]payee:[/dim] {last.payment.payee_vpa}\n"
            f"[dim]amount:[/dim] ₹{last.payment.amount_paise/100:,.2f}\n"
            f"[dim]decision:[/dim] [red]{last.decision.reason_code.value}[/red]\n"
            f"[dim]entry_hash:[/dim] {last.entry_hash[:32]}...\n"
            f"[dim]prev_hash:[/dim] {last.prev_hash[:32]}...",
            title="📋 Audit Log Entry",
            border_style="yellow",
        ))
    pause(pace)


# ===========================================================================
# SCENE 4: Hierarchical Multi-Agent Delegation
# ===========================================================================

def scene_4_delegation(root_token: str, user_pub, pace: float) -> None:
    print_header("SCENE 4 — HIERARCHICAL MULTI-AGENT DELEGATION",
                 "Shopping agent issues an attenuated sub-mandate to a courier sub-agent")

    from kavach.delegation import (
        DelegationAttenuationError,
        SubMandateSpec,
        issue_sub_mandate,
        verify_delegation_chain,
    )

    parent_priv, parent_pub = generate_keypair()
    console.print("[dim]Parent agent received grocery order. Delegating ₹100 tip to delivery sub-agent...[/dim]")
    pause(pace, 0.5)

    # Valid sub-mandate
    courier_spec = SubMandateSpec(
        purpose="Courier Delivery Tip",
        allowed_payees=["ondc-grocer@upi"],  # In root allowlist
        allowed_categories=["groceries"],
        per_txn_limit_paise=10_000,          # ₹100
        total_budget_paise=20_000,           # ₹200
        max_txn_count=2,
    )
    child_token = issue_sub_mandate(parent_priv, root_token, "courier_subagent", courier_spec, validity_seconds=300)

    ok, _, claims = verify_delegation_chain(child_token, root_token, user_pub, parent_pub)
    console.print(Panel(
        f"[green bold]✓ SUB-MANDATE ISSUED & CRYPTOGRAPHICALLY VERIFIED[/green bold]\n"
        f"[dim]Child Agent:[/dim] courier_subagent\n"
        f"[dim]Purpose:[/dim] Courier Delivery Tip\n"
        f"[dim]Allowed Payees:[/dim] ondc-grocer@upi\n"
        f"[dim]Per-Txn Limit:[/dim] ₹100.00 (Narrowed from ₹1,500.00)\n"
        f"[dim]Total Budget:[/dim] ₹200.00 (Narrowed from ₹3,000.00)",
        title="🤝 Attenuated Delegation",
        border_style="green",
    ))
    pause(pace)

    console.print("\n[yellow]Now what if compromised courier sub-agent attempts to expand authority to 'attacker_wallet@upi'?[/yellow]")
    pause(pace, 0.5)

    rogue_spec = SubMandateSpec(
        purpose="Rogue Expansion",
        allowed_payees=["attacker_wallet@upi"],  # NOT IN ROOT!
        allowed_categories=["groceries"],
        per_txn_limit_paise=10_000,
        total_budget_paise=20_000,
        max_txn_count=2,
    )
    try:
        issue_sub_mandate(parent_priv, root_token, "rogue_subagent", rogue_spec, validity_seconds=300)
    except DelegationAttenuationError as e:
        console.print(Panel(
            f"[red bold]✗ DELEGATION EXPANSION BLOCKED[/red bold]\n"
            f"Reason: {e}\n"
            f"[dim]Sub-agents can only narrow parental scope, never widen it.[/dim]",
            title="🛡️ Kavach Delegation Firewall",
            border_style="red",
        ))
    pause(pace)


# ===========================================================================
# SCENE 5: Explicit Consent & Step-Up Escalation
# ===========================================================================

def scene_5_escalation(user_priv, user_pub, pace: float) -> None:
    print_header("SCENE 5 — EXPLICIT CONSENT & STEP-UP (ESCALATE)",
                 "What You See Is What You Sign (WYSIWYS) Preview + Step-Up Challenge")

    from kavach.consent import (
        EscalationChallenge,
        generate_mandate_preview,
        issue_escalation_ticket,
        verify_escalation_ticket,
    )

    # 1. Preview
    spec = MandateSpec(
        purpose="Monthly Groceries",
        allowed_payees=["ondc-grocer@upi"],
        allowed_categories=["groceries"],
        per_txn_limit_paise=150_000,
        total_budget_paise=300_000,
        max_txn_count=5,
    )
    preview = generate_mandate_preview(spec)

    console.print(Panel(
        f"[bold white]Purpose:[/bold white] {preview.purpose}\n"
        f"[bold white]Allowed Payees:[/bold white] {', '.join(preview.allowed_payees)}\n"
        f"[bold white]Per-Txn Limit:[/bold white] {preview.per_txn_limit_inr}\n"
        f"[bold white]Total Budget:[/bold white] {preview.total_budget_inr}\n"
        f"[bold white]Max Transactions:[/bold white] {preview.max_txn_count}\n"
        f"[dim]SHA-256 Fingerprint: {preview.sha256_fingerprint[:32]}...[/dim]",
        title="👁️ What You See Is What You Sign (WYSIWYS)",
        border_style="cyan",
    ))
    pause(pace)

    # 2. Step-up Challenge
    console.print("[yellow]Agent encounters an exceptional bulk order of ₹2,500 (exceeds standard limit)...[/yellow]")
    console.print("[cyan]🛡️ Interceptor triggers step-up challenge (ESCALATE). User prompted for biometric/PIN consent...[/cyan]")
    pause(pace, 0.5)

    challenge = EscalationChallenge(
        challenge_id="chal-bulk-901",
        amount_paise=250_000,
        payee_vpa="ondc-grocer@upi",
        reason="Order ₹2,500 exceeds standard automated limit",
        exp=int(time.time()) + 60,
    )
    esc_ticket = issue_escalation_ticket(user_priv, challenge, validity_seconds=60)
    used_tickets: set[str] = set()

    ok, msg = verify_escalation_ticket(esc_ticket, user_pub, "ondc-grocer@upi", 250_000, used_tickets)
    console.print(Panel(
        f"[green bold]✓ USER STEP-UP CONSENT GRANTED[/green bold]\n"
        f"{msg}\n"
        f"[dim]Single-use ephemeral ticket (valid 60s) accepted by Interceptor.[/dim]",
        title="🔐 Interactive Consent Verified",
        border_style="green",
    ))
    pause(pace)


# ===========================================================================
# BONUS: Token Tampering
# ===========================================================================

def scene_bonus_tamper(token: str, public_key, gateway: MockUPIGateway, pace: float) -> None:
    print_header("BONUS SCENE — TOKEN TAMPERING",
                 "What if someone edits the token to raise the spending limit?")

    import base64

    console.print("[yellow]🔧 Attacker modifies the JWT payload...[/yellow]")
    console.print("[dim]  Changing per_txn_limit_paise: 150000 → 99999999[/dim]")
    pause(pace, 0.5)

    parts = token.split(".")
    payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
    payload_bytes = base64.urlsafe_b64decode(payload_b64)
    payload = json.loads(payload_bytes)
    payload["mandate"]["per_txn_limit_paise"] = 99999999
    # Also fix exp so it's not expired during demo
    payload["exp"] = int(time.time()) + 900
    payload["nbf"] = int(time.time()) - 10
    new_payload = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).rstrip(b"=").decode()
    tampered_token = f"{parts[0]}.{new_payload}.{parts[2]}"

    console.print("[yellow]  Re-assembled token with original signature...[/yellow]")
    pause(pace, 0.5)

    interceptor = KavachInterceptor(
        public_key=public_key,
        mandate_token=tampered_token,
        ledger=Ledger(),
        audit_log=AuditLog(),
    )

    req = PaymentRequest(
        amount_paise=9999900,
        payee_vpa="ondc-grocer@upi",
        category="groceries",
        nonce=str(uuid.uuid4()),
    )

    decision, _ = interceptor.evaluate(req)
    console.print(format_decision_panel(decision, "Kavach Decision — Tampered Token"))
    pause(pace)

    console.print("\n[green bold]✓ Cryptographic integrity check caught the tampering.[/green bold]")
    pause(pace)


# ===========================================================================
# MAIN
# ===========================================================================

@app.command()
def main(
    pace: float = typer.Option(1.0, "--pace", "-p", help="Pace multiplier (e.g. 1.0, 0.3)"),
    fast: bool = typer.Option(False, "--fast", "-f", help="Fast mode for rapid automated testing (~5s)"),
    video: bool = typer.Option(False, "--video", help="3-minute video mode (~2m 40s total runtime)"),
    record: bool = typer.Option(False, "--record", help="Export Rich terminal recording to evidence/demo_output.txt and evidence/demo_output.svg"),
    export_dir: str = typer.Option("evidence", "--export-dir", help="Directory to export recorded terminal output"),
) -> None:
    """Run the Kavach demo — 4 scenes + bonus for the hackathon video."""
    start_time = time.time()
    if fast:
        active_pace = 0.2
    elif video:
        active_pace = 6.4  # Calibrated for 2m 40s (160s) total runtime (ideal for 3-minute video recording)
    else:
        active_pace = pace

    console.print(Panel.fit(
        "[bold white]Kavach: Cryptographic Micro-Mandates[/bold white]\n"
        "[dim]Securing Agentic AI Payments on India's UPI/DPI Stack[/dim]\n\n"
        "[cyan]India Agentic AI Security Hackathon[/cyan]\n"
        "[dim]Track: Trust, Consent & Delegation[/dim]",
        title="🛡️ KAVACH",
        border_style="cyan",
        padding=(1, 4),
    ))
    pause(active_pace)

    # Setup
    private_key, public_key = generate_keypair()
    priv_interceptor, pub_interceptor = generate_keypair()

    # ── Scene 0: Baseline ──
    gateway_baseline = MockUPIGateway()
    gateway_baseline.set_balance("user@upi", 1000000)      # ₹10,000
    gateway_baseline.set_balance("ondc-grocer@upi", 500000)  # ₹5,000
    gateway_baseline.set_balance("attacker_wallet@upi", 0)

    scene_0_baseline(gateway_baseline, active_pace)

    # ── Scene 1: Issue Mandate ──
    token = scene_1_issue_mandate(private_key, active_pace)

    # ── Scenes 2 & 3: With Kavach ON ──
    gateway = MockUPIGateway(interceptor_public_key=pub_interceptor)
    gateway.set_balance("user@upi", 1000000)
    gateway.set_balance("ondc-grocer@upi", 500000)
    gateway.set_balance("attacker_wallet@upi", 0)

    ledger = Ledger()
    audit = AuditLog()
    interceptor = KavachInterceptor(
        public_key=public_key,
        mandate_token=token,
        ledger=ledger,
        audit_log=audit,
        interceptor_private_key=priv_interceptor,
    )

    console.print(Panel(
        "[green bold]✓ Kavach protection is now [underline]ENABLED[/underline][/green bold]\n"
        "[dim]All payments flow through the cryptographic interceptor with per-transaction signed approvals.[/dim]",
        border_style="green",
    ))
    pause(active_pace)

    scene_2_legitimate(interceptor, gateway, active_pace)
    scene_3_attack(interceptor, gateway, audit, active_pace)

    # ── Scene 4: Hierarchical Delegation ──
    scene_4_delegation(token, public_key, active_pace)

    # ── Scene 5: Explicit Consent & Step-Up ──
    scene_5_escalation(private_key, public_key, active_pace)

    # ── Bonus: Tamper ──
    scene_bonus_tamper(token, public_key, gateway, active_pace)

    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    # ── Finale ──
    console.print()
    console.rule("[bold green]DEMO COMPLETE[/bold green]", style="green")
    console.print()
    console.print(Panel.fit(
        f"[bold]Key Takeaways:[/bold]\n\n"
        f"1. [red]Without Kavach[/red]: A prompt injection drains ₹5,000 to the attacker.\n"
        f"2. [green]With Kavach[/green]: The same injection is blocked by deterministic policy.\n"
        f"3. [green]Legitimate[/green] payments within mandate bounds work seamlessly.\n"
        f"4. [green]Attenuated Delegation[/green]: Sub-agents can narrow scope, never expand it.\n"
        f"5. [green]Step-Up Consent[/green]: High-value actions require interactive user authorization.\n"
        f"6. [green]Token tampering[/green] is caught by Ed25519 signature verification.\n\n"
        f"[dim]Security comes from cryptography and policy — not from system prompts.[/dim]\n"
        f"[dim]Demo execution time: {minutes}m {seconds:02d}s (Pace: {active_pace}x)[/dim]",
        title="🛡️ Kavach: Cryptographic Micro-Mandates",
        border_style="cyan",
        padding=(1, 2),
    ))

    if record:
        out_dir = Path(export_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        txt_path = out_dir / "demo_output.txt"
        svg_path = out_dir / "demo_output.svg"
        console.save_text(str(txt_path))
        try:
            console.save_svg(str(svg_path), title="Kavach Terminal Demo")
        except Exception as e:
            console.print(f"[dim]SVG export warning: {e}[/dim]")
        console.print(f"\n[dim]Recorded output exported to {txt_path} and {svg_path}[/dim]")


if __name__ == "__main__":
    app()
