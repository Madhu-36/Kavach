# SAFL Hackathon Criteria Evidence Matrix

**Event**: India Agentic AI Security Hackathon (Secure AI Futures Lab)  
**Track**: Trust, Consent & Delegation  
**Submission**: Kavach: Cryptographic Micro-Mandates for Agentic AI  
**Repository**: `c:\Users\Asus\OneDrive\Desktop\PROJECTS\Kavach`  

---

## 1. Judging Criteria Mapping

| Rubric Criterion | Weight | What Judges Look For | Concrete Repo Evidence & File Paths |
| :--- | :---: | :--- | :--- |
| **Technical Depth** | **40%** | • Fully working implementation, zero hand-waving.<br>• Deterministic cryptographic enforcement (Ed25519, EdDSA).<br>• Formal property-based tests (Hypothesis) covering edge cases and clock skew.<br>• Resilient concurrency and atomic transactions.<br>• Fail-closed error handling and crash resilience. | • [`src/kavach/`](file:///c:/Users/Asus/OneDrive/Desktop/PROJECTS/Kavach/src/kavach): Complete Python implementation.<br>• [`src/kavach/ledger_db.py`](file:///c:/Users/Asus/OneDrive/Desktop/PROJECTS/Kavach/src/kavach/ledger_db.py): SQLite WAL `BEGIN IMMEDIATE` atomic reservations.<br>• [`tests/test_invariants.py`](file:///c:/Users/Asus/OneDrive/Desktop/PROJECTS/Kavach/tests/test_invariants.py): Hypothesis property tests over 6 formal invariants (I1–I6).<br>• [`evidence/pytest_report.xml`](file:///c:/Users/Asus/OneDrive/Desktop/PROJECTS/Kavach/evidence/pytest_report.xml): 71+ automated tests passing with JUnit XML proof.<br>• [`tests/test_redteam.py`](file:///c:/Users/Asus/OneDrive/Desktop/PROJECTS/Kavach/tests/test_redteam.py): 50-thread concurrent stress test asserting zero budget overshoot. |
| **Novelty & Creativity** | **20%** | • Pioneering defense against indirect prompt injections on payment rails.<br>• Applying NPCI UPI e-Mandate/AutoPay to autonomous AI agents.<br>• Attenuated hierarchical delegation (sub-mandates).<br>• Human-in-the-loop dynamic consent escalation (ESCALATE). | • [`docs/RELATED_WORK.md`](file:///c:/Users/Asus/OneDrive/Desktop/PROJECTS/Kavach/docs/RELATED_WORK.md): Systematic positioning against Google AP2, Visa/Mastercard agentic tokens, Macaroons/Biscuits, and NPCI AutoPay.<br>• [`src/kavach/delegation.py`](file:///c:/Users/Asus/OneDrive/Desktop/PROJECTS/Kavach/src/kavach/delegation.py): Attenuated sub-mandate chaining with root budget accounting.<br>• [`src/kavach/consent.py`](file:///c:/Users/Asus/OneDrive/Desktop/PROJECTS/Kavach/src/kavach/consent.py): Human-readable mandate preview & step-up PIN/biometric escalation tickets. |
| **Presentation & Communication** | **20%** | • Clear, transparent threat model and architecture documentation.<br>• High-visibility, accessible terminal demo calibrated to 3 minutes.<br>• Honest reporting of model evasion and worst-case baselines.<br>• Comprehensive FAQ addressing tough engineering trade-offs. | • [`README.md`](file:///c:/Users/Asus/OneDrive/Desktop/PROJECTS/Kavach/README.md): Multi-tier threat model, architecture diagrams, and quickstart.<br>• [`SUBMISSION.md`](file:///c:/Users/Asus/OneDrive/Desktop/PROJECTS/Kavach/SUBMISSION.md): SAFL form answers with citations to evidence files, plus timed video script.<br>• [`docs/JUDGE_QA.md`](file:///c:/Users/Asus/OneDrive/Desktop/PROJECTS/Kavach/docs/JUDGE_QA.md): 15 challenging judge questions answered directly.<br>• [`demo.py`](file:///c:/Users/Asus/OneDrive/Desktop/PROJECTS/Kavach/demo.py): Interactive terminal demo with rupee formatting and `--video` flag (2m 40s). |
| **Mission Fit (DPI & India Alignment)** | **20%** | • Practical integration with India's UPI and ONDC digital rail specifications.<br>• Auditable, tamper-evident forensic accounting for consumer protection.<br>• Zero-cost adoption for banks and PSP switches. | • [`src/kavach/mock_upi.py`](file:///c:/Users/Asus/OneDrive/Desktop/PROJECTS/Kavach/src/kavach/mock_upi.py): UPI switch simulation supporting ticket verification.<br>• [`src/kavach/mock_ondc.py`](file:///c:/Users/Asus/OneDrive/Desktop/PROJECTS/Kavach/src/kavach/mock_ondc.py): ONDC product catalog with indirect injection payloads.<br>• [`src/kavach/audit.py`](file:///c:/Users/Asus/OneDrive/Desktop/PROJECTS/Kavach/src/kavach/audit.py): Append-only SHA-256 hash-chained JSONL audit ledger.<br>• Integer-paise precision: eliminates floating-point financial bugs across all Indian payment rails. |

---

## 2. Evidence Traceability Directory (`evidence/`)

All project claims in documentation, submission texts, and walkthroughs trace directly to raw, reproducible test artifacts:

- `evidence/pytest_output.txt`: Raw stdout of the complete test suite.
- `evidence/pytest_report.xml`: Machine-readable JUnit XML test report.
- `evidence/eval_benchmark.json`: Raw trial-by-trial logs for 40 evaluation trials across 5 injection categories.
- `evidence/demo_timing.txt`: Wall-clock timing output of `demo.py` verifying video pace calibration (2:38).
- `evidence/secret_scan.txt`: Gitleaks / regex secret scanner clean output.
- `evidence/fresh_clone.txt`: Clean-environment venv installation and verification log.

---

## 3. Timeboxing & Contingency Triage (Deadline Plan)

**Active Deadline**: Final Submission Day (October 31, 2026).

To guarantee submission-grade excellence without risking deadline overruns, the plan is triaged strictly by priority:

- **Must-Have (P0 & P1)**: 
  - Evidence integrity pipeline (`evidence/` generator).
  - Hypothesis invariant tests (`I1` to `I6`) and crash test.
  - Complete removal of legacy secret paths and information leakage fixes.
  - Multi-reason code regression fix and clean fresh-clone test.
  - Real live eval benchmark reporting with Clopper-Pearson confidence bounds.
- **High-Impact Track Features (P2)**:
  - Attenuated sub-mandates (hierarchical agent delegation).
  - Dynamic user consent escalation (ESCALATE) with one-time step-up authorization.
  - Docker Compose multi-network setup (with documented fallback if local engine is offline).
- **Adoption & Polish (P3)**:
  - Developer ergonomic wrapper `kavach.guard` decorator for Python/LangChain tools.
  - Calibrated demo scenes.
- **Triage Cuts (If Deadline Demands)**:
  1. *Cut 1*: Single-page local HTML dashboard (FastAPI static web UI) — replaced by robust CLI and terminal Rich tables.
  2. *Cut 2*: Signed merchant quotes extension (P2.4) — retained as a well-documented threat model extension in `docs/JUDGE_QA.md`.
