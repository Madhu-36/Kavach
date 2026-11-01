"""Generate and validate the Attack-to-Test Traceability Matrix from pytest collection."""
from __future__ import annotations

import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

# The threat model rows mapping vector -> Invariant -> Defensive Mechanism -> Test target
ATTACK_CATALOG = [
    (
        "**Indirect Prompt Injection**",
        "**I2**",
        "Byte-level payee allowlist containment",
        "test_agent.py::TestScriptedCompromisedAgent::test_injection_attack_blocked",
    ),
    (
        "**Payee Lookalikes & Homoglyphs**",
        "**I2**",
        "Strict UPI regex & exact string match",
        "test_redteam.py::TestPayeeLookalikes::test_trailing_space_rejected",
    ),
    (
        "**Runaway Price Spike**",
        "**I1**",
        "Per-transaction limit cap (`per_txn_limit_paise`)",
        "test_policy.py::TestPerTxnLimit::test_amount_over_per_txn_limit",
    ),
    (
        "**Salami-Slicing Drain**",
        "**I1**",
        "Atomic ledger tracking cumulative spend",
        "test_redteam.py::TestSalamiSlicing::test_salami_slicing_bounded",
    ),
    (
        "**Transaction Spamming**",
        "**I3**",
        "Atomic transaction count ceiling",
        "test_policy.py::TestTxnCountLimit::test_txn_count_exceeded",
    ),
    (
        "**Temporal Clock Skew & Expiry**",
        "**I4**",
        "Unix epoch validation against `nbf` and `exp`",
        "test_invariants.py::TestInvariant4TemporalValidity::test_i4_temporal_validity_boundaries",
    ),
    (
        "**Immediate Revocation**",
        "**I4**",
        "Real-time `jti` lookup in revocation ledger",
        "test_invariants.py::TestInvariant4TemporalValidity::test_i4_revocation_immediately_blocks_all_spend",
    ),
    (
        "**Ticket Replay**",
        "**I5**",
        "Gateway single-use nonce & ticket cache",
        "test_invariants.py::TestInvariant5Bijectivity::test_i5_exact_bijective_mapping_and_replay_rejection",
    ),
    (
        "**Ticket Parameter Tampering**",
        "**I5**",
        "Gateway verifies ticket payload against request",
        "test_redteam.py::TestGatewaySecurity::test_tampered_ticket_payee_rejected",
    ),
    (
        "**Direct Gateway Bypass**",
        "**I5**",
        "Gateway fails closed (403) without valid ticket",
        "test_mock_upi.py::TestMockUPIGateway::test_gateway_without_keys_refuses_pay",
    ),
    (
        "**Concurrency Race Condition**",
        "**I1, I3**",
        "SQLite `BEGIN IMMEDIATE` two-phase reservation",
        "test_redteam.py::TestConcurrencyAndLedger::test_atomic_concurrency_stress",
    ),
    (
        "**Mid-Flight Interceptor Crash**",
        "**I6**",
        "Automatic reservation rollback on exception",
        "test_invariants.py::TestInvariant6CrashResilience::test_i6_midflight_crash_rolls_back_reservation",
    ),
    (
        "**Interceptor Unreachable**",
        "**I6**",
        "Client stub fails closed with zero balance moved",
        "test_invariants.py::TestInvariant6CrashResilience::test_i6_client_fails_closed_when_interceptor_unreachable",
    ),
    (
        "**Token Tampering**",
        "**I1-I4**",
        "Ed25519 digital signature verification",
        "test_policy.py::TestTamperedPayload::test_tampered_payload_rejected",
    ),
    (
        "**Algorithm Confusion (HS256)**",
        "**I1-I4**",
        "Strict algorithm pinning to `EdDSA`",
        "test_policy.py::TestAlgConfusion::test_hs256_with_public_key_rejected",
    ),
    (
        "**Audit Trail Tampering**",
        "**I5**",
        "SHA-256 hash-chaining across JSONL entries",
        "test_audit.py::TestAuditLogIntegrity::test_tampered_amount_detected",
    ),
    (
        "**Information Leakage**",
        "—",
        "Redacted agent errors (no allowlists/budgets)",
        "test_redteam.py::TestInformationLeakageAndReasonCodes::test_agent_facing_error_contains_no_sensitive_leaks",
    ),
    (
        "**Sub-Agent Authority Expansion**",
        "—",
        "Monotonic attenuation verification",
        "test_delegation.py::TestDelegationAttenuation::test_widening_payee_raises_attenuation_error",
    ),
]


def collect_tests() -> set[str]:
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        check=True,
    )
    collected = set()
    for line in res.stdout.splitlines():
        line = line.strip()
        if "::" in line:
            normalized = line.replace("\\", "/").split("tests/")[-1]
            collected.add(normalized)
            collected.add(line)
    return collected


def main():
    collected = collect_tests()
    print(f"Collected items from pytest.")

    missing = []
    lines = [
        "| Threat Vector | Invariant | Defensive Mechanism | Passing Test Suite |",
        "| :--- | :---: | :--- | :--- |",
    ]

    for vector, invariant, mechanism, test_path in ATTACK_CATALOG:
        matched = False
        for c in collected:
            if test_path in c or (test_path.split("::")[0] in c and test_path.split("::")[-1] in c):
                matched = True
                break
        if not matched:
            missing.append(test_path)
        parts = test_path.split("::")
        short_name = f"`{parts[0]}::{parts[-1]}`"
        lines.append(f"| {vector} | {invariant} | {mechanism} | {short_name} |")

    if missing:
        print(f"ERROR: Missing tests in suite: {missing}", file=sys.stderr)
        sys.exit(1)

    print("\nGenerated Attack-to-Test Table:")
    table_md = "\n".join(lines)
    print(table_md)
    return table_md


if __name__ == "__main__":
    main()
