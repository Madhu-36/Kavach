"""Kavach Static Attack Catalog Regression Suite.

Migrated from eval/run_adaptive_attacker.py into a formal pytest regression suite.
Tests 30 distinct attack strategies against Kavach, verifying 100% fail-closed defense:
- Rounds 1-5: Payee spoofing & homoglyphs (trailing space, .evil domain, @ confusion)
- Rounds 6-7: Negative & zero amount edge cases
- Rounds 8-9: Pydantic schema injection (skip_checks, override token)
- Rounds 10-12: JWT algorithm confusion (alg: none, HS256 key confusion)
- Rounds 13-17: Ticket replay, tampering, parameter binding mismatches
- Rounds 18-20: Salami-slicing budget exhaustion
- Rounds 21-30: Nonce collision, unicode homoglyphs, timing skew, and parameter fuzzing
"""

from __future__ import annotations

import pytest

from eval.run_adaptive_attacker import run_adaptive_simulation


class TestStaticAttackCatalog:
    """Regression test suite verifying zero bypasses across all 30 attack strategies."""

    def test_all_30_attack_rounds_fail_closed(self):
        rounds = run_adaptive_simulation()
        assert len(rounds) == 30, f"Expected 30 attack rounds, found {len(rounds)}"

        bypasses = [r for r in rounds if r.bypassed]
        assert len(bypasses) == 0, f"Detected {len(bypasses)} bypassed attack vectors: {[b.strategy_name for b in bypasses]}"

    @pytest.mark.parametrize("round_idx", list(range(30)))
    def test_individual_attack_strategy_blocked(self, round_idx: int):
        rounds = run_adaptive_simulation()
        r = rounds[round_idx]
        assert r.bypassed is False, (
            f"Attack vector '{r.strategy_name}' (Round {r.round_num}) bypassed defense!\n"
            f"Observed feedback: {r.observed_feedback}"
        )
