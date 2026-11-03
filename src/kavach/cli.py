"""Typer CLI for Kavach — keygen, issue, revoke, verify-log."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from kavach.audit import AuditLog
from kavach.issuer import (
    decode_mandate_unsafe,
    generate_keypair,
    load_private_key,
    save_keypair,
    sign_mandate,
)
from kavach.models import MandateSpec

console = Console()
app = typer.Typer(
    name="kavach",
    help="Kavach: Cryptographic Micro-Mandates for Agentic AI",
    add_completion=False,
)

DEFAULT_KEY_DIR = Path.home() / ".kavach" / "keys"


@app.command()
def keygen(
    output_dir: Path = typer.Option(DEFAULT_KEY_DIR, "--dir", "-d", help="Output directory for keys"),
) -> None:
    """Generate an Ed25519 keypair for signing mandates."""
    private_key, public_key = generate_keypair()
    priv_path, pub_path = save_keypair(private_key, public_key, output_dir)

    rprint(Panel.fit(
        f"[green]✓ Keypair generated[/green]\n"
        f"  Private key: [bold]{priv_path}[/bold]\n"
        f"  Public key:  [bold]{pub_path}[/bold]\n\n"
        f"[yellow]⚠ Keep the private key secret![/yellow]",
        title="🔑 Kavach Keygen",
        border_style="green",
    ))


@app.command()
def issue(
    agent_id: str = typer.Option("agent_001", "--agent", "-a", help="Agent ID (sub claim)"),
    purpose: str = typer.Option("grocery shopping", "--purpose", "-p"),
    payees: str = typer.Option("ondc-grocer@upi", "--payees", help="Comma-separated UPI VPAs"),
    categories: str = typer.Option("groceries,essentials", "--categories", "-c"),
    per_txn_limit: int = typer.Option(150000, "--per-txn", help="Per-txn limit in paise"),
    total_budget: int = typer.Option(300000, "--budget", help="Total budget in paise"),
    max_txn_count: int = typer.Option(5, "--max-txn", help="Max transaction count"),
    validity: int = typer.Option(900, "--validity", "-v", help="Validity in seconds"),
    key_dir: Path = typer.Option(DEFAULT_KEY_DIR, "--key-dir", "-k"),
    output_file: Optional[Path] = typer.Option(None, "--output", "-o", help="Save token to file"),
) -> None:
    """Issue a signed mandate JWT."""
    priv_path = key_dir / "kavach_private.pem"
    if not priv_path.exists():
        rprint(f"[red]✗ Private key not found at {priv_path}. Run `kavach keygen` first.[/red]")
        raise typer.Exit(1)

    private_key = load_private_key(priv_path)
    mandate = MandateSpec(
        purpose=purpose,
        allowed_payees=[p.strip() for p in payees.split(",")],
        allowed_categories=[c.strip() for c in categories.split(",") if c.strip()],
        per_txn_limit_paise=per_txn_limit,
        total_budget_paise=total_budget,
        max_txn_count=max_txn_count,
    )

    token = sign_mandate(
        private_key=private_key,
        agent_id=agent_id,
        mandate=mandate,
        validity_seconds=validity,
    )

    # Display decoded claims
    claims = decode_mandate_unsafe(token)
    table = Table(title="📜 Mandate Claims", show_header=True)
    table.add_column("Claim", style="cyan")
    table.add_column("Value", style="white")
    for key, value in claims.items():
        if key == "mandate":
            for mk, mv in value.items():
                table.add_row(f"  mandate.{mk}", str(mv))
        else:
            table.add_row(key, str(value))
    console.print(table)

    if output_file:
        output_file.write_text(token)
        rprint(f"\n[green]✓ Token saved to {output_file}[/green]")
    else:
        rprint(f"\n[dim]Token: {token[:60]}...[/dim]")

    rprint(f"\n[green]✓ Mandate issued for agent '{agent_id}'[/green]")


@app.command()
def revoke(
    jti: str = typer.Argument(help="The jti (mandate ID) to revoke"),
    revocation_file: Path = typer.Option(
        Path.home() / ".kavach" / "revoked.json",
        "--file", "-f",
    ),
) -> None:
    """Revoke a mandate by its jti."""
    revoked: list[str] = []
    if revocation_file.exists():
        revoked = json.loads(revocation_file.read_text())

    if jti not in revoked:
        revoked.append(jti)
        revocation_file.parent.mkdir(parents=True, exist_ok=True)
        revocation_file.write_text(json.dumps(revoked, indent=2))
        rprint(f"[green]✓ Mandate {jti} revoked[/green]")
    else:
        rprint(f"[yellow]Mandate {jti} was already revoked[/yellow]")


@app.command(name="verify-log")
def verify_log(
    log_file: Path = typer.Argument(help="Path to the audit log JSONL file"),
) -> None:
    """Verify the integrity of a hash-chained audit log."""
    is_valid, message = AuditLog.verify(log_file)

    if is_valid:
        rprint(Panel.fit(
            f"[green]✓ {message}[/green]",
            title="🔗 Audit Log Verification",
            border_style="green",
        ))
    else:
        rprint(Panel.fit(
            f"[red]✗ TAMPER DETECTED: {message}[/red]",
            title="🔗 Audit Log Verification",
            border_style="red",
        ))
        raise typer.Exit(1)


@app.command(name="voice")
def voice_listen(
    engine: str = typer.Option("auto", "--engine", "-e", help="ASR engine: 'whisper', 'google', or 'auto'"),
    model: str = typer.Option("base.en", "--model", "-m", help="Whisper model size (e.g. tiny.en, base.en)"),
    timeout: float = typer.Option(10.0, "--timeout", "-t", help="Listening timeout in seconds"),
    device: Optional[str] = typer.Option(None, "--device", "-d", help="Microphone device name or index (e.g. 'realtek' or '1')"),
    execute: bool = typer.Option(False, "--execute", "-x", help="Authorize payment through Kavach security gateway"),
) -> None:
    """Listen to spoken payment commands via microphone and extract structured Kavach intents."""
    from kavach.voice_listener import AudioConfig, VoiceListener

    config = AudioConfig(input_device=device)
    listener = VoiceListener(model_size=model, config=config)

    rprint(Panel.fit(
        "[bold cyan]Kavach Voice Listener & Semantic Intent Extractor (Advanced DSP)[/bold cyan]\n"
        "[dim]85-7500Hz Bandpass • Spectral Denoising • Dual VAD (RMS+ZCR) • 500ms Ring Buffer • Whisper ASR[/dim]\n\n"
        f"• Engine: [bold]{engine}[/bold] (Whisper model: [green]{model}[/green])\n"
        f"• Device: [bold]{device or 'Default Microphone'}[/bold]\n"
        f"• Timeout: [bold]{timeout}s[/bold] | Gateway Execution: [bold]{execute}[/bold]",
        title="🎙️ Voice Interface",
        border_style="cyan",
    ))

    intent = listener.listen_and_extract(engine=engine, timeout_sec=timeout)

    # Format extracted claims into rich table
    table = Table(title="🎯 Extracted Spoken Intent", show_header=True)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Action", intent.action)
    table.add_row("Raw Transcript", f'"{intent.raw_transcript}"')
    table.add_row("Payee VPA", str(intent.payee_vpa))
    table.add_row("Amount (Rupees)", f"₹{intent.amount_rupees:,.2f}")
    table.add_row("Amount (Paise)", f"{intent.amount_paise} paise")
    table.add_row("Note / Purpose", intent.note or "N/A")
    table.add_row("Confidence", f"{intent.confidence * 100:.1f}%")
    console.print(table)

    if execute and intent.action == "make_payment":
        rprint("\n[bold yellow]🔐 Authorizing payment intent via Kavach Security Gateway...[/bold yellow]")
        auth_result = listener.authorize_with_kavach(intent)

        if auth_result["allowed"]:
            rprint(Panel.fit(
                f"[green]✓ PAYMENT APPROVED[/green]\n"
                f"  Reason Code:     [bold]{auth_result['reason_code']}[/bold]\n"
                f"  Message:         [dim]{auth_result['message']}[/dim]\n"
                f"  Approval Ticket: [bold cyan]{auth_result['approval_ticket'][:40]}...[/bold cyan]\n"
                f"  Mandate Token:   [dim]{auth_result['mandate_token'][:40]}...[/dim]",
                title="🛡️ Kavach Gateway Decision",
                border_style="green",
            ))
        else:
            rprint(Panel.fit(
                f"[red]✗ PAYMENT DENIED[/red]\n"
                f"  Reason Code: [bold]{auth_result['reason_code']}[/bold]\n"
                f"  Message:     [yellow]{auth_result['message']}[/yellow]",
                title="🛡️ Kavach Gateway Decision",
                border_style="red",
            ))


@app.command(name="voice-check")
def voice_check(
    duration: float = typer.Option(3.0, "--duration", "-d", help="Calibration recording duration in seconds"),
    device: Optional[str] = typer.Option(None, "--device", help="Specific microphone device name or index"),
) -> None:
    """Diagnose microphone acoustics: measure noise floor, speech peak, SNR, and clipping risk."""
    from kavach.voice_listener import AudioConfig, VoiceListener

    config = AudioConfig(input_device=device)
    listener = VoiceListener(config=config)

    rprint(Panel.fit(
        "[bold cyan]Kavach Acoustic Microphone Diagnostic[/bold cyan]\n"
        "[dim]Measuring Noise Floor, Headroom, Signal-to-Noise Ratio (SNR), and Clipping Risk[/dim]",
        title="🔍 Acoustic Diagnostic",
        border_style="cyan",
    ))

    print("Measuring ambient room noise for 1s...")
    listener.calibrate_ambient_noise(duration_sec=1.0)
    diag = listener.diagnose_microphone(duration_sec=duration)

    table = Table(title="📊 Microphone Acoustic Metrics", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Measurement", style="white")

    table.add_row("Device Index", str(diag["device_index"] if diag["device_index"] is not None else "Default [1]"))
    table.add_row("Noise Floor", f"{diag['ambient_noise_db']} dBFS")
    table.add_row("Speech Peak", f"{diag['peak_db']} dBFS")
    table.add_row("Signal-to-Noise Ratio (SNR)", f"[bold green]{diag['snr_db']} dB[/bold green]")
    table.add_row("Clipping Risk", "[red]YES - CLIPPING DETECTED[/red]" if diag["is_clipping"] else "[green]NO (Safe Headroom)[/green]")
    table.add_row("Acoustic Status", diag["recommendation"])

    console.print(table)


if __name__ == "__main__":
    app()
