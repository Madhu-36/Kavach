# Kavach: System & Environment Status (Truth Audit)

**Date**: 2026-10-31  
**Project**: Kavach: Cryptographic Micro-Mandates for Agentic AI  
**Track**: Trust, Consent & Delegation (India Agentic AI Security Hackathon)

---

## 1. Environment & Runtime Status

| Component | Host State | Project Status | Verification Method |
| :--- | :--- | :--- | :--- |
| **Operating System** | Windows 11 (build 26100) | Supported (cross-platform pure Python) | `sys.platform == 'win32'` |
| **Python Version** | Python 3.14.0 (64-bit) | Verified | `python --version` |
| **Cryptographic Engine** | `cryptography` 50.0.2 + `PyJWT` 2.15.1 | Ed25519 / EdDSA Active | `tests/test_policy.py` |
| **Local LLM Engine** | Ollama running at `http://127.0.0.1:11434` (`llama3.1:latest`, 8.0B) | Active for Live LLM Evaluations | `eval/run_eval.py`, `eval/run_adaptive_attacker_llm.py` |
| **Cloud LLM Providers** | API keys not provided in host environment | Pluggable via `.env` (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) | `.env.example` verified |
| **Process Isolation** | Independent OS processes & distinct network ports | **VERIFIED** on host | `scripts/run_isolated_demo.py` (Ports 8000 & 8100) |
| **Container Isolation (Docker)** | Docker CLI v29.7.2 present, Docker Desktop engine stopped | **DESIGN-ONLY** (production blueprint provided) | `docs/DOCKER_ISOLATION.md`, `docker-compose.yml` |
| **Signed Merchant Quotes** | Protocol specification and message schemas defined | **DESIGN-ONLY** (staged for future phase) | `docs/CLAIMS.md` |
| **Git Repository** | Initialized, master branch, tagged `v1.0.0` | Active | `git status`, `git log` |

---

## 2. Trust Boundary Architecture

1. **User (Mandate Issuer)**:
   - Holds user Ed25519 private key.
   - Signs unforgeable mandate token with deterministic spending invariants.
   - Private key never exported, never passed to agent or interceptor.

2. **Interceptor (Security Gateway / Local Service)**:
   - Holds user Ed25519 public key and its own Ed25519 interceptor private key.
   - Evaluates all payment requests against mandate policy and atomic SQLite ledger (`BEGIN IMMEDIATE`).
   - If allowed, generates and signs a 30-second single-use ticket bound to exact payee, exact amount, and cryptographic nonce.
   - Fails closed on any policy violation, expired mandate, or tampered signature.

3. **UPI Gateway**:
   - Holds interceptor Ed25519 public key.
   - Verifies ticket signature and ensures single-use nonce has not been spent.
   - Strictly rejects any direct calls or mismatched parameters.

4. **Agent (LLM Runtime)**:
   - Fully isolated client stub.
   - Holds **NO** mandate tokens, **NO** private keys, **NO** interceptor signing keys.
   - Receives only generic, non-leaking block messages upon denial (`SECURITY_BLOCK: <REASON_CODES>`).

---

## 3. Honest Disclosure of Known Limitations

- **Docker Containers**: Docker network isolation (`docker-compose.yml`) is provided as a production blueprint but is marked **DESIGN-ONLY** because the local Docker Desktop daemon is not running on this development machine. Process-level isolation via separate local ports is verified.
- **Signed Merchant Quotes**: The defense against ONDC catalog price poisoning relies on strict per-transaction and total budget caps. Merchant-signed catalog quotes remain **DESIGN-ONLY**.
- **Network UPI Latency**: The mock gateway runs in-process or over local loopback HTTP (`< 15ms`). Production UPI network round-trips across NPCI switches typically introduce 200–500 ms external latency.
