"""Tests for the hash-chained audit log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kavach.audit import AuditLog
from kavach.models import Decision, PaymentRequest, ReasonCode


@pytest.fixture
def log_path(tmp_path) -> Path:
    return tmp_path / "audit.jsonl"


@pytest.fixture
def populated_log(log_path) -> AuditLog:
    """An audit log with 5 entries."""
    log = AuditLog(path=log_path)
    for i in range(5):
        log.log_decision(
            mandate_jti=f"jti-{i}",
            payment=PaymentRequest(
                amount_paise=10000 * (i + 1),
                payee_vpa="ondc-grocer@upi",
                category="groceries",
                nonce=f"audit-nonce-{i}",
            ),
            decision=Decision.approve(f"Payment {i} approved"),
        )
    return log


class TestAuditLogIntegrity:
    def test_valid_log_passes_verification(self, populated_log, log_path):
        is_valid, msg = AuditLog.verify(log_path)
        assert is_valid is True
        assert "5 entries verified" in msg

    def test_tampered_amount_detected(self, populated_log, log_path):
        """Modify an amount in the middle of the log — should be detected."""
        lines = log_path.read_text().strip().splitlines()
        entry = json.loads(lines[2])
        entry["payment"]["amount_paise"] = 999999  # tamper!
        lines[2] = json.dumps(entry)
        log_path.write_text("\n".join(lines) + "\n")

        is_valid, msg = AuditLog.verify(log_path)
        assert is_valid is False
        assert "Hash mismatch" in msg or "Chain broken" in msg

    def test_deleted_entry_detected(self, populated_log, log_path):
        """Delete an entry from the middle — should break the chain."""
        lines = log_path.read_text().strip().splitlines()
        del lines[2]  # Remove entry at index 2
        log_path.write_text("\n".join(lines) + "\n")

        is_valid, msg = AuditLog.verify(log_path)
        assert is_valid is False

    def test_reordered_entries_detected(self, populated_log, log_path):
        """Swap two entries — should break the chain."""
        lines = log_path.read_text().strip().splitlines()
        lines[1], lines[2] = lines[2], lines[1]
        log_path.write_text("\n".join(lines) + "\n")

        is_valid, msg = AuditLog.verify(log_path)
        assert is_valid is False

    def test_empty_log_valid(self, tmp_path):
        empty_path = tmp_path / "empty.jsonl"
        empty_path.write_text("")
        is_valid, msg = AuditLog.verify(empty_path)
        assert is_valid is True

    def test_hash_chain_continuity(self, populated_log):
        """Each entry's prev_hash should match the previous entry's entry_hash."""
        entries = populated_log.entries
        for i in range(1, len(entries)):
            assert entries[i].prev_hash == entries[i - 1].entry_hash


class TestAuditLogAppend:
    def test_entries_have_sequential_seq(self, populated_log):
        for i, entry in enumerate(populated_log.entries):
            assert entry.seq == i

    def test_denied_decisions_logged(self, log_path):
        log = AuditLog(path=log_path)
        log.log_decision(
            mandate_jti="jti-denied",
            payment=PaymentRequest(
                amount_paise=500000,
                payee_vpa="attacker_wallet@upi",
                category="groceries",
            ),
            decision=Decision.deny(
                ReasonCode.PAYEE_NOT_ALLOWED, "Payee not allowed"
            ),
        )
        assert len(log.entries) == 1
        assert log.entries[0].decision.allowed is False
