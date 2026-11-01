# Kavach: Cryptographic Micro-Mandates for Agentic AI 🛡️

> **Kavach guarantees mathematical spending safety for autonomous AI agents on digital payment rails.**  
> By pairing asymmetric Ed25519 micro-mandates with strict OS/network-level process isolation, Kavach ensures that even a fully compromised agent (via prompt injection, tool tampering, or hallucination) cannot, within the stated threat model, move money outside the user's signed cryptographic boundary.  
> *Developed for the India Agentic AI Security Hackathon (Secure AI Futures Lab) — Trust, Consent & Delegation Track.*

[![CI Matrix](https://github.com/Madhu-36/Kavach/actions/workflows/ci.yml/badge.svg)](https://github.com/Madhu-36/Kavach/actions)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Invariants Verified](https://img.shields.io/badge/Invariants-I1--I4_Hypothesis,_I5--I6_Crash_Tests-success.svg)](docs/INVARIANTS.md)
[![Tests Passing](https://img.shields.io/badge/Tests-137_Passed-brightgreen.svg)](evidence/pytest_output.txt)

---

## 📌 The Core Problem: Probabilistic Models on Deterministic Rails

Autonomous AI agents are being granted financial spending power—procuring groceries on ONDC, booking travel, or paying utility bills. However, granting an LLM direct API credentials or banking access creates an existential financial hazard:
- **Indirect Prompt Injection**: Malicious instructions hidden in product catalogs, reviews, or seller metadata can force the agent to redirect funds to attacker wallets.
- **Model Hallucination & Runaway Loops**: A model can invent prices or enter recursive payment loops.
- **System Prompts are NOT Security**: System prompts operate in the probabilistic semantic layer. An adversary can always discover jailbreaks that trick an LLM into ignoring prior instructions.

**Kavach eliminates reliance on model behavior.** Inspired by NPCI's UPI AutoPay and e-Mandate architecture, Kavach introduces **cryptographic micro-mandates**: short-lived, Ed25519-signed policy tokens that deterministically enforce payees, categories, per-transaction limits, cumulative budgets, and transaction counts outside the agent's context.

---

## 🏛️ Multi-Tier Architecture & Threat Model

Kavach enforces defense-in-depth across **three distinct physical isolation tiers**:

```mermaid
flowchart TD
    subgraph "Process 1: User Device (Local Trust Boundary)"
        U["👤 User (CLI / App)"] -->|"kavach keygen"| UK["🔑 User Ed25519 Private Key<br/>(NEVER LEAVES USER DEVICE)"]
        UK -->|"kavach issue"| M["📜 Signed Mandate JWT<br/>(Allowed payees, limits, budget)"]
    end

    subgraph "Process 2: Untrusted Agent Runtime (:8200) [Tier A]"
        M -.->|"Injected into Interceptor Service<br/>(ISOLATED from Agent)"| SVR
        ONDC["🏪 Mock ONDC Store<br/>(Poisoned Catalog Listing)"] -->|"Catalog search results +<br/>Indirect Prompt Injection"| A["🤖 LLM Agent<br/>(Live Gemini/Ollama or Scripted)"]
        A -->|"Tool call: make_upi_payment<br/>(extra='forbid')"| CLI["Kavach Thin Client Stub<br/>• Holds ZERO private keys<br/>• Holds ZERO mandate tokens"]
    end

    subgraph "Process 3: Isolated Kavach Interceptor Service (:8100) [Tier B]"
        CLI -->|"HTTP RPC: POST /evaluate"| SVR["🛡️ Kavach Interceptor"]
        SVR -->|"Verify Mandate Signature"| UPK["User Public Key"]
        SVR -->|"Atomic Reservation"| SQL[("SQLite Ledger<br/>BEGIN IMMEDIATE")]
        SVR -->|"If Policy Passes: Sign Ticket"| IK["🔑 Interceptor Private Key"]
        IK -->|"Signed EdDSA Ticket<br/>(jti, vpa, amount, nonce, exp: 30s)"| TKT["🎫 Ephemeral Approval Ticket"]
        SVR -->|"Append Decision"| AUDIT[("📋 SHA-256 Hash-Chained<br/>JSONL Audit Trail")]
    end

    subgraph "Process 4: Mock UPI Gateway / Switch (:8000) [Tier C]"
        CLI -->|"POST /pay + Ticket Header"| GW["🏦 UPI Gateway Switch"]
        GW -->|"Verify Ticket Signature"| IPK["Interceptor Public Key"]
        GW -->|"Enforce Single-Use Nonce"| CACHE["Ticket Replay Cache"]
        GW -->|"Settle Balances"| BAL["Account Balances"]
    end

    style SVR fill:#1b4332,stroke:#2d6a4f,color:#fff
    style A fill:#780000,stroke:#c1121f,color:#fff
    style GW fill:#003049,stroke:#669bbc,color:#fff
    style SQL fill:#495057,stroke:#212529,color:#fff
```

### Threat Tiers & Mitigations
- **Tier A (Agent Process Compromise)**: Assume the LLM and its process memory are 100% controlled by an attacker. The agent holds zero private keys, zero public keys, and zero tokens. Its tool schema strictly forbids extra parameters (`extra='forbid'`).
- **Tier B (Interceptor-Agent Channel)**: The agent communicates with the interceptor exclusively over an unprivileged RPC stub. The interceptor evaluates 11 pure deterministic checks independently and issues signed ephemeral tickets. Redacted error messages prevent information leakage.
- **Tier C (DPI / UPI Payment Rail)**: The gateway accepts payment requests *only* with an authentic Ed25519 `ApprovalTicket` signed by the interceptor. Architectural Docker network topology (`agent_net` vs `gateway_net`) separates container subnets in design.

---

## 🔒 Attack-to-Test Traceability Matrix

Every attack vector identified in our threat model is verified by dedicated automated tests:

| Threat Vector | Invariant | Defensive Mechanism | Passing Test Suite |
| :--- | :---: | :--- | :--- |
| **Indirect Prompt Injection** | **I2** | Byte-level payee allowlist containment | `test_agent.py::test_injection_attack_blocked` |
| **Payee Lookalikes & Homoglyphs** | **I2** | Strict UPI regex & exact string match | `test_redteam.py::test_trailing_space_rejected` |
| **Runaway Price Spike** | **I1** | Per-transaction limit cap (`per_txn_limit_paise`) | `test_policy.py::test_amount_over_per_txn_limit` |
| **Salami-Slicing Drain** | **I1** | Atomic ledger tracking cumulative spend | `test_redteam.py::test_salami_slicing_bounded` |
| **Transaction Spamming** | **I3** | Atomic transaction count ceiling | `test_policy.py::test_txn_count_exceeded` |
| **Temporal Clock Skew & Expiry** | **I4** | Unix epoch validation against `nbf` and `exp` | `test_invariants.py::test_i4_temporal_validity_boundaries` |
| **Immediate Revocation** | **I4** | Real-time `jti` lookup in revocation ledger | `test_invariants.py::test_i4_revocation_immediately_blocks_all_spend` |
| **Ticket Replay** | **I5** | Gateway single-use nonce & ticket cache | `test_invariants.py::test_i5_exact_bijective_mapping_and_replay_rejection` |
| **Ticket Parameter Tampering** | **I5** | Gateway verifies ticket payload against request | `test_redteam.py::test_tampered_ticket_payee_rejected` |
| **Direct Gateway Bypass** | **I5** | Gateway fails closed (403) without valid ticket | `test_mock_upi.py::test_gateway_without_keys_refuses_pay` |
| **Concurrency Race Condition** | **I1, I3** | SQLite `BEGIN IMMEDIATE` two-phase reservation | `test_redteam.py::test_atomic_concurrency_stress` |
| **Mid-Flight Interceptor Crash** | **I6** | Automatic reservation rollback on exception | `test_invariants.py::test_i6_midflight_crash_rolls_back_reservation` |
| **Interceptor Unreachable** | **I6** | Client stub fails closed with zero balance moved | `test_invariants.py::test_i6_client_fails_closed_when_interceptor_unreachable` |
| **Token Tampering** | **I1-I4** | Ed25519 digital signature verification | `test_policy.py::test_tampered_payload_rejected` |
| **Algorithm Confusion (HS256)** | **I1-I4** | Strict algorithm pinning to `EdDSA` | `test_policy.py::test_hs256_with_public_key_rejected` |
| **Audit Trail Tampering** | **I5** | SHA-256 hash-chaining across JSONL entries | `test_audit.py::test_tampered_amount_detected` |
| **Information Leakage** | — | Redacted agent errors (no allowlists/budgets) | `test_redteam.py::test_agent_facing_error_contains_no_sensitive_leaks` |
| **Sub-Agent Authority Expansion** | — | Monotonic attenuation verification | `test_delegation.py::test_widening_payee_raises_attenuation_error` |

---

## 📊 Live Evaluation Benchmark Results

We benchmarked Kavach across empirical LLM evaluations (`llama3.1:latest` 8B via Ollama), adaptive red-team evaluations, and deterministic attack demonstrations:

| Benchmark Tier | Mode / Condition | Trials (N) | Baseline (Protection OFF) | Kavach (Protection ON) | Efficacy & Security Impact |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Live LLM Benchmark (Default)** | 5 Injections $\times$ 10 Trials | 100 | 0.0% Attacked [0.0%–7.1%] | **0.0% Attacked** [0.0%–7.1%] | 100% Exposure; 0/50 obeyed by base model |
| **Positive Control Verification** | Direct Instruction to pay Attacker | 20 | 100.0% Attack Executed (10/10) | **0.0% Attack Executed** (0/10) | **Blocked 10/10 (95% CI 69.2-100%)** |
| **Real LLM Adaptive Attacker** | 7 Threat Vectors $\times$ 10 Attempts | 70 | — | **0.0% Bypassed (0/70)** | **5.1% Upper Bound at 95% Confidence** |
| **Deterministic Worst-Case Demo** | Scripted 100% Compromised Model | 40 | ₹5,000.00 / attempt drained | **₹0.00 Lost** | **100% Interception (0/20 bypassed)** |
| **Evaluation Overhead** | Micro-benchmark (p50 / p99) | 1,000 | — | **1.092 ms / 3.609 ms** | In-process p50 1.09 ms, RPC p50 7.08 ms |

### Adaptive Attacker Vector $\times$ Outcome Matrix ($N=70$)

An adversarial LLM agent (`llama3.1:latest`) iteratively probed Kavach across 7 attack vectors with full parameter variation and tool calling:

| Vector | AMOUNT_EXCEEDS_PER_TXN_LIMIT | APPROVED (In-Mandate) | BUDGET_EXHAUSTED | CATEGORY_NOT_ALLOWED | PAYEE_NOT_ALLOWED | TIMEOUT / NO_TOOL_CALL | Total |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **V1: Payee Substitution & Lookalikes** | 0 | 0 | 0 | 0 | 10 | 0 | 10 |
| **V2: Allowed-Payee Overspend** | 2 | 3 | 5 | 0 | 0 | 0 | 10 |
| **V3: Count Exhaustion** | 0 | 0 | 0 | 0 | 8 | 2 | 10 |
| **V4: Budget Exhaustion** | 0 | 0 | 0 | 0 | 10 | 0 | 10 |
| **V5: Replay Attacks** | 0 | 0 | 0 | 0 | 10 | 0 | 10 |
| **V6: Argument Smuggling & Type Tampering** | 0 | 0 | 0 | 2 | 8 | 0 | 10 |
| **V7: Category Mismatch** | 0 | 0 | 0 | 0 | 10 | 0 | 10 |
| **TOTAL** | **2** | **3** | **5** | **2** | **56** | **2** | **70** |

- **Bypass Definition**: A bypass is strictly defined as any payment request approved by the interceptor that pays an unauthorized payee or exceeds mandate parameters (limits, budget, count, category, or nonces).
- **Approved In-Mandate Payments**: 3 payments to allowed merchant `ondc-grocer@upi` within limit (₹1,500) and budget (cumulative spend 300,000 paise $\le$ 300,000 paise limit). These are expected legitimate operations, not bypasses.
- **Unreached Attempts**: 2 attempts in Vector 3 did not reach the policy layer due to local socket timeouts during Ollama LLM tool-calling generation.

*Full machine-generated tables, exact Clopper-Pearson confidence intervals, and JSONL transcripts are documented in [`eval/RESULTS.md`](eval/RESULTS.md) and [`evidence/`](evidence/).*

---

## ⚡ 5-Command Quickstart

```bash
# 1. Clone repository
git clone https://github.com/Madhu-36/Kavach.git
cd Kavach

# 2. Install package in virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

# 3. Run complete test suite (137 tests passing)
python -m pytest tests/ -v

# 4. Run interactive Rich terminal demo (Fast mode)
python demo.py --fast

# 5. Run full evidence generation pipeline
python scripts/generate_evidence.py
```

---

## 🛡️ Developer Adoption: `@kavach.guard`

Protect any payment function or LangChain tool in **3 lines of code**:

```python
from kavach.guard import guard

@guard(interceptor_url="http://127.0.0.1:8100", category="groceries")
def buy_groceries(amount_paise: int, payee_vpa: str, note: str = ""):
    # This sensitive code executes ONLY if the Kavach Interceptor approves!
    return upi_gateway.transfer(amount_paise, payee_vpa)
```
*See runnable examples in [`examples/basic_shopping.py`](examples/basic_shopping.py) and [`examples/langchain_agent.py`](examples/langchain_agent.py).*

---

## 📚 Deep-Dive Documentation

- [Verified Claims Matrix & Truth Audit](docs/CLAIMS.md)
- [System & Environment Status](docs/STATUS.md)
- [Formal Security Invariants (I1 - I6)](docs/INVARIANTS.md)
- [OS & Network-Level Container Isolation](docs/DOCKER_ISOLATION.md)
- [Comparative Analysis & Related Work](docs/RELATED_WORK.md)
- [Judge Q&A & Technical Defense Guide](docs/JUDGE_QA.md)
- [Signed Merchant Quotes Specification](docs/MERCHANT_QUOTES.md)
- [Evaluation Results & Methodology](eval/RESULTS.md)
- [Submission Form & Video Script](SUBMISSION.md)
- [Verification Evidence Directory](evidence/)

---

## ⚠️ Honest Limitations & Design Boundaries

Kavach enforces deterministic cryptographic limits at the payment boundary, but real-world deployment entails specific boundaries:

1. **In-Mandate Abuse**: An attacker or misaligned agent cannot pay unauthorized payees, but could trigger unneeded or wasteful purchases to an authorized merchant up to the mandate's total budget limit.
2. **Compromised Allowed Seller (Merchant Collusion)**: If an allowed seller's listing or infrastructure is compromised, fraudulent price inflation within the authorized limits cannot be caught by payment-level checks alone (proposed mitigation (design-only, not implemented): signed merchant quotes; see [`docs/MERCHANT_QUOTES.md`](docs/MERCHANT_QUOTES.md)).
3. **Liveness & Denial of Service**: A runaway or compromised agent could rapidly consume the mandate's transaction count or total budget on small purchases, exhausting the mandate and starving legitimate tasks.
4. **Ledger Sync Across Distributed Hosts**: SQLite `BEGIN IMMEDIATE` with WAL mode provides robust concurrency safety on a single node. Scaling interceptors horizontally across distributed clusters requires an external consensus store (e.g. distributed Raft or Spanner/Postgres) to avoid split-brain budget exhaustion.
5. **Revocation Latency & Propagation**: While local revocation check is instantaneous ($O(1)$ lookup in memory/database), propagating a revocation event across distributed offline agents or partitioned network edges carries network propagation latency.
6. **Key Storage & Hardware Root of Trust**: Keys in this prototype are Ed25519 PEM files. Production banking deployments must anchor user and interceptor private keys in Hardware Security Modules (HSM), TPM 2.0, or device Secure Enclaves (FIDO2 / WebAuthn) rather than OS filesystem storage.
7. **Client-Side vs. Switch-Level Enforcement**: Kavach runs as an isolated local proxy service (`:8100`) between the agent and UPI gateway (`:8000`). For absolute unbypassability in open wild environments, the bank's UPI switch or payment gateway itself must reject settlement of any transaction lacking an authentic, single-use Kavach approval ticket.

