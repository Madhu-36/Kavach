# Formal Security Invariants of Kavach

Kavach guarantees mathematical spending safety for autonomous AI agents on digital payment rails through **six deterministic, non-negotiable invariants**. Regardless of model hallucination, indirect prompt injection, multi-turn adversarial jailbreaking, or concurrency race conditions, these invariants hold at all times.

---

## Invariant Definitions

### Invariant 1 (I1): Total Budget Invariance
$$\sum_{i=1}^{k} \text{ApprovedPaise}_i \le \text{TotalBudgetPaise}$$
*Across any arbitrary sequence of payment requests (including interleaved, concurrent, or adversarial requests), the cumulative sum of settled funds authorized under mandate $M$ shall never exceed $\text{TotalBudgetPaise}$.*
- **Mechanism**: Atomic SQLite reservation with `BEGIN IMMEDIATE` and cumulative commitment tracking.
- **Fail-Closed Condition**: If $\sum \text{Paise} + \text{NewPaise} > \text{TotalBudgetPaise}$, the interceptor immediately issues `BUDGET_EXHAUSTED` and discards reservations.

---

### Invariant 2 (I2): Strict Payee Containment
$$\forall \text{ Approved Transaction } T, \quad \text{PayeeVPA}(T) \in \text{AllowedPayees}(M)$$
*No funds may be transferred to any UPI Virtual Payment Address (VPA) not explicitly enumerated in the user's signed mandate allowlist.*
- **Mechanism**: Byte-level exact equality check against `allowed_payees` combined with strict UPI syntax regex (`^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{2,64}$`).
- **Fail-Closed Condition**: Case mismatch, trailing whitespace, lookalike extensions (`.evil`), or secondary `@` symbols trigger `PAYEE_NOT_ALLOWED`.

---

### Invariant 3 (I3): Transaction Count Ceiling
$$\text{Count}(\text{ApprovedTransactions}) \le \text{MaxTxnCount}(M)$$
*The total number of approved transactions executed under mandate $M$ shall never exceed $\text{MaxTxnCount}$.*
- **Mechanism**: Incremental transaction counter committed within the atomic ledger.
- **Fail-Closed Condition**: Any request arriving after $\text{Count} \ge \text{MaxTxnCount}$ triggers `TXN_COUNT_EXCEEDED`.

---

### Invariant 4 (I4): Temporal Validity & Non-Revocation
$$\forall \text{ Approved Transaction } T \text{ evaluated at time } t: \quad \text{nbf}(M) \le t < \text{exp}(M) \quad \land \quad \text{jti}(M) \notin \text{RevokedList}$$
*No payment may be approved before the mandate's activation timestamp ($\text{nbf}$), on or after its expiration timestamp ($\text{exp}$), or if its unique identifier ($\text{jti}$) has been added to the revocation ledger.*
- **Mechanism**: Unix epoch clock validation against JWT standard claims and real-time lookup in the `revoked_jtis` set.
- **Fail-Closed Condition**: Violations trigger `MANDATE_NOT_YET_VALID`, `MANDATE_EXPIRED`, or `MANDATE_REVOKED`.

---

### Invariant 5 (I5): Ticket & Audit Bijectivity
$$\forall \text{ Gateway Settle } S \iff \exists! \text{ Valid ApprovalTicket } K \iff \exists! \text{ AuditEntry } A$$
*For every payment settled by the UPI gateway, there exists exactly one single-use, cryptographically verified approval ticket signed by the interceptor, and exactly one corresponding SHA-256 hash-chained entry in the persistent audit log.*
- **Mechanism**: 
  - Asymmetric Ed25519 digital signature on ephemeral approval tickets.
  - Gateway ticket cache preventing replay attacks.
  - SHA-256 hash chaining: $\text{Hash}_i = \text{SHA256}(\text{Hash}_{i-1} \parallel \text{Entry}_i)$.
- **Fail-Closed Condition**: Replays, ticket signature errors, or parameter mismatches result in `HTTP 403 Forbidden`.

---

### Invariant 6 (I6): Fail-Closed Liveness Boundary
$$\text{Status}(\text{Interceptor}) \ne \text{HEALTHY} \implies \text{GatewaySettlements} = 0$$
*If the Kavach interceptor service, database, or cryptographic signing engine crashes, drops connection, or encounters an internal exception, zero payment transactions can settle at the gateway.*
- **Mechanism**: The UPI gateway strictly requires an unexpired, freshly signed approval ticket from the interceptor. The agent cannot forge this ticket.
- **Fail-Closed Condition**: Unhandled runtime exceptions roll back pending ledger reservations and return `INTERNAL_ERROR`.

---

## Formal Property-Based Testing Strategy

Invariants `I1` through `I6` are subjected to automated randomized verification via **Hypothesis** in `tests/test_invariants.py`:
- Random arrays of payment amounts (from 1 paisa to $10^9$ paise).
- Random payees (valid, invalid, malformed, lookalike).
- Random execution sequences and simulated clock skew.
- Mid-flight process crash tests verifying reservation rollback and double-spend immunity.
