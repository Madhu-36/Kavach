# Comparative Analysis: Kavach vs. Prior Art in Agentic Payments & Delegation

This document positions **Kavach** within the broader landscape of agentic payments, cryptographic capability tokens, and Digital Public Infrastructure (DPI).

---

## 1. Comparative Architectural Matrix

| Capability / Feature | Google AP2 (Agent Payments) | Visa/Mastercard Agentic Tokens | Macaroons / Biscuit Tokens | NPCI UPI AutoPay (e-Mandate) | **Kavach (Cryptographic Micro-Mandates)** |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Primary Domain** | Browser / Web Checkout | Card Networks (EMVCo) | Distributed Auth / Capabilities | Bank Account Pulls (DPI) | **Autonomous Agentic Spending (UPI/ONDC)** |
| **Enforcement Mechanism** | Prompt & API Orchestration | Card Tokenization Gateway | Cryptographic Caveats (HMAC/PubKey) | Bank Core Banking System (CBS) | **Asymmetric Ed25519 Micro-Mandates + Process Firewall** |
| **Deterministic Guarantee** | ❌ Heuristic (LLM-in-the-loop) | ⚠️ Partial (Gateway MCC filters) | ✅ Pure Cryptographic Proof | ✅ Bank Rule Engine | ✅ **Pure Cryptographic Proof + Fail-Closed OS Isolation** |
| **Granular Invariant Controls** | Basic order amount | Merchant category / velocity | Custom predicate attenuations | Recurring fixed amount / frequency | **Payee Allowlist, Category, Per-Txn, Cumulative, Count, Time Window** |
| **Multi-Agent Hierarchical Delegation** | ❌ None | ❌ Single Token Bound | ✅ Supported (Caveat chaining) | ❌ Single Recurring Mandate | ✅ **Attenuated Sub-Mandates (Monotonic Scope Narrowing)** |
| **Protection Against Compromised Agent** | ❌ Fails (Agent holds keys/context) | ⚠️ Fails if agent has card PAN/token | ⚠️ Fails if token leaked to agent | ⚠️ Fails if agent holds UPI PIN | ✅ **Mathematically Immune (Agent holds zero keys/tokens)** |
| **Interactive Step-Up (ESCALATE)** | Browser modal prompt | 3-D Secure / SMS OTP | Out of band policy | UPI PIN challenge | ✅ **Ephemeral Ed25519 Consent Ticket (60s validity)** |
| **Auditability & Non-Repudiation** | Server logs | Card settlement ledger | Token evaluation trace | Bank statement | ✅ **Append-Only SHA-256 Hash-Chained Audit Ledger** |

---

## 2. Detailed Technical Comparison

### 2.1 Google AP2 (Agent Payment Protocol)
Google AP2 represents an emerging protocol designed to standardize payment intents between conversational AI agents and e-commerce websites.
- **Strengths**: High user convenience in chat interfaces; standardizes shopping cart exchange schemas.
- **Vulnerabilities**: AP2 delegates security largely to the conversational model and user confirmation dialogues. If an agent suffers from indirect prompt injection (e.g. poisoning within an item description), the LLM can construct deceptive confirmation summaries or spoof user intent.
- **How Kavach Differs**: Kavach treats the agent as **fully adversarial by default**. Even if an AP2 agent is completely compromised, it cannot execute a payment because the Kavach Interceptor independently verifies signed Ed25519 mandates against a strict mathematical policy before communicating with the payment rail.

### 2.2 Visa & Mastercard Agentic Card Tokens
Both major payment networks have announced agentic tokenization initiatives adapting EMVCo network tokens for AI assistants.
- **Strengths**: Leverages existing global credit/debit acceptance infrastructure; supports dynamic merchant category code (MCC) restrictions.
- **Vulnerabilities**: Designed for credit/debit card pull networks where chargebacks and fraud scoring occur post-settlement. Real-time account-to-account push networks (like India's UPI) settle irrevocably within 200 milliseconds. Post-facto fraud detection is insufficient for instant account drainage.
- **How Kavach Differs**: Kavach is built natively for instant push payment rails (UPI / IMPS / FedNow / Pix). It enforces mathematical pre-flight spending containment, guaranteeing zero irrevocable fund loss before a transaction touches the banking switch.

### 2.3 Macaroons and Biscuit Tokens
Macaroons (Google Research) and Biscuit tokens are capability-based authorization tokens that support decentralized delegation via cryptographic caveats.
- **Strengths**: First-class support for offline attenuation: a token holder can append caveat blocks to narrow permissions without contacting the issuer.
- **Limitations**: Traditional macaroons use symmetric HMAC chains, meaning any verification service that validates the token must possess the shared secret, creating key management vulnerabilities. Furthermore, caveat languages verify request parameters in isolation and lack native atomic cumulative budget state tracking across concurrent transactions.
- **How Kavach Differs**: Kavach combines asymmetric Ed25519 digital signatures (so the interceptor only needs the public key) with an atomic two-phase reservation ledger (`SqliteLedger` with `BEGIN IMMEDIATE`). This bridges stateless cryptographic tokens with stateful, race-condition-free financial balance tracking.

### 2.4 NPCI UPI AutoPay & e-Mandate
National Payments Corporation of India (NPCI) introduced UPI AutoPay for recurring subscription debits (OTT platforms, utility bills, SIP investments).
- **Strengths**: Trusted regulatory framework approved by the Reserve Bank of India (RBI); high adoption across 350+ banks.
- **Limitations**: Designed for fixed, predictable recurring subscriptions (e.g., ₹499 deducted on the 5th of every month). It cannot handle dynamic, autonomous micro-spending workflows required by AI agents (e.g., "spend up to ₹2,000 across any combination of 3 ONDC grocery merchants over the next 48 hours, capped at ₹800 per order").
- **How Kavach Differs**: Kavach adapts the trusted UPI e-Mandate paradigm into **dynamic, cryptographically verifiable micro-mandates**. By pairing asymmetric user authorization with sub-millisecond policy verification, Kavach delivers the missing security primitives needed to bring autonomous AI agents onto India's Digital Public Infrastructure (DPI).
