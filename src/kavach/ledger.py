"""Ledger — tracks cumulative spending and transaction counts per mandate (jti).

Thread-safe, in-memory with optional JSON persistence.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional


class Ledger:
    """In-memory ledger for per-mandate spending tracking."""

    def __init__(self, persist_path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._persist_path = persist_path
        # jti -> {"spent_paise": int, "txn_count": int}
        self._data: dict[str, dict[str, int]] = {}
        if persist_path and persist_path.exists():
            self._load()

    def _load(self) -> None:
        """Load ledger from disk."""
        if self._persist_path:
            raw = self._persist_path.read_text()
            self._data = json.loads(raw)

    def _save(self) -> None:
        """Persist ledger to disk."""
        if self._persist_path:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(json.dumps(self._data, indent=2))

    def get_spent(self, jti: str) -> int:
        """Return total paise spent under this mandate."""
        with self._lock:
            return self._data.get(jti, {}).get("spent_paise", 0)

    def get_count(self, jti: str) -> int:
        """Return number of transactions under this mandate."""
        with self._lock:
            return self._data.get(jti, {}).get("txn_count", 0)

    def record_txn(self, jti: str, amount_paise: int) -> None:
        """Record a successful transaction."""
        with self._lock:
            if jti not in self._data:
                self._data[jti] = {"spent_paise": 0, "txn_count": 0}
            self._data[jti]["spent_paise"] += amount_paise
            self._data[jti]["txn_count"] += 1
            self._save()

    def reset(self, jti: Optional[str] = None) -> None:
        """Reset ledger for a specific mandate or all mandates."""
        with self._lock:
            if jti:
                self._data.pop(jti, None)
            else:
                self._data.clear()
            self._save()
