"""Hash-chained JSONL audit log.

Each entry includes the SHA-256 hash of the previous entry, forming an
append-only chain that detects tampering.
Hardened for Phase 2 to store all violated reason codes and approval ticket IDs.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from kavach.models import AuditEntry, Decision, PaymentRequest


_GENESIS_HASH = "0" * 64  # The prev_hash of the first entry.


class AuditLog:
    """Append-only, hash-chained JSONL audit log."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._path = path
        self._seq = 0
        self._last_hash = _GENESIS_HASH
        self._entries: list[AuditEntry] = []

        if path and path.exists():
            self._load()

    def _load(self) -> None:
        """Replay existing log to reconstruct chain state."""
        assert self._path is not None
        lines = self._path.read_text(encoding="utf-8").strip().splitlines()
        for line in lines:
            if not line.strip():
                continue
            entry = AuditEntry(**json.loads(line))
            self._entries.append(entry)
            self._seq = entry.seq + 1
            self._last_hash = entry.entry_hash

    @staticmethod
    def _compute_hash(data: str, prev_hash: str) -> str:
        """SHA-256 hash of data + prev_hash."""
        content = f"{prev_hash}|{data}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def log_decision(
        self,
        mandate_jti: str,
        payment: PaymentRequest,
        decision: Decision,
        approval_ticket_id: Optional[str] = None,
    ) -> AuditEntry:
        """Append a decision to the audit log with all reason codes and hashes."""
        with self._lock:
            ts = datetime.now(timezone.utc).isoformat()

            # Canonical JSON payload (excluding computed entry_hash and prev_hash)
            payload = json.dumps(
                {
                    "seq": self._seq,
                    "timestamp": ts,
                    "mandate_jti": mandate_jti,
                    "payment": payment.model_dump(),
                    "decision": decision.model_dump(),
                    "approval_ticket_id": approval_ticket_id,
                },
                sort_keys=True,
            )

            entry_hash = self._compute_hash(payload, self._last_hash)

            entry = AuditEntry(
                seq=self._seq,
                timestamp=ts,
                mandate_jti=mandate_jti,
                payment=payment,
                decision=decision,
                entry_hash=entry_hash,
                prev_hash=self._last_hash,
                approval_ticket_id=approval_ticket_id,
            )

            self._entries.append(entry)
            self._last_hash = entry_hash
            self._seq += 1

            if self._path:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(entry.model_dump_json() + "\n")

            return entry

    @property
    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def verify_chain(self) -> bool:
        """Verify the cryptographic hash-chain integrity of loaded entries."""
        prev_hash = _GENESIS_HASH
        for i, entry in enumerate(self._entries):
            if entry.seq != i or entry.prev_hash != prev_hash:
                return False
            payload = json.dumps(
                {
                    "seq": entry.seq,
                    "timestamp": entry.timestamp,
                    "mandate_jti": entry.mandate_jti,
                    "payment": entry.payment.model_dump(),
                    "decision": entry.decision.model_dump(),
                    "approval_ticket_id": entry.approval_ticket_id,
                },
                sort_keys=True,
            )
            expected_hash = self._compute_hash(payload, prev_hash)
            if entry.entry_hash != expected_hash:
                return False
            prev_hash = entry.entry_hash
        return True

    @staticmethod
    def verify(path: Path) -> tuple[bool, str]:
        """Verify the integrity of an audit log file.

        Returns (is_valid, message).
        """
        if not path.exists():
            return False, "Audit log file not found"

        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return True, "Audit log is empty"

        lines = content.splitlines()
        prev_hash = _GENESIS_HASH

        for i, line in enumerate(lines):
            if not line.strip():
                continue
            entry = AuditEntry(**json.loads(line))

            if entry.seq != i:
                return False, f"Sequence gap at entry {i}: expected seq={i}, got seq={entry.seq}"

            if entry.prev_hash != prev_hash:
                return (
                    False,
                    f"Chain broken at entry {i}: expected prev_hash={prev_hash[:16]}..., "
                    f"got {entry.prev_hash[:16]}...",
                )

            # Recompute the canonical payload hash
            payload = json.dumps(
                {
                    "seq": entry.seq,
                    "timestamp": entry.timestamp,
                    "mandate_jti": entry.mandate_jti,
                    "payment": entry.payment.model_dump(),
                    "decision": entry.decision.model_dump(),
                    "approval_ticket_id": entry.approval_ticket_id,
                },
                sort_keys=True,
            )
            expected_hash = AuditLog._compute_hash(payload, prev_hash)

            if entry.entry_hash != expected_hash:
                return (
                    False,
                    f"Hash mismatch at entry {i}: expected {expected_hash[:16]}..., "
                    f"got {entry.entry_hash[:16]}...",
                )

            prev_hash = entry.entry_hash

        return True, f"All {len(lines)} entries verified — chain intact ✓"
