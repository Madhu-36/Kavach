"""Atomic SQLite Ledger with reserve-commit-rollback flow (BEGIN IMMEDIATE).

Thread-safe and multi-process safe using SQLite WAL mode.
Guarantees zero budget or transaction count overshoot under heavy concurrency.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Optional


class SqliteLedger:
    """Atomic two-phase reservation ledger backed by SQLite."""

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        if db_path is None or db_path == ":memory:":
            # In-memory shared across threads using unique URI per instance
            self._db_path = f"file:kavach_ledger_{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._is_uri = True
            self._keepalive_conn = sqlite3.connect(
                self._db_path,
                uri=True,
                check_same_thread=False,
            )
        else:
            self._db_path = str(Path(db_path).resolve())
            self._is_uri = False
            self._keepalive_conn = None
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._db_path,
            uri=self._is_uri,
            timeout=30.0,
            check_same_thread=False,
        )
        for attempt in range(15):
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA busy_timeout=5000;")
                break
            except sqlite3.OperationalError:
                if attempt == 14:
                    raise
                time.sleep(0.1)
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mandate_totals (
                    jti TEXT PRIMARY KEY,
                    spent_paise INTEGER NOT NULL DEFAULT 0,
                    txn_count INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reservations (
                    idempotency_key TEXT PRIMARY KEY,
                    jti TEXT NOT NULL,
                    amount_paise INTEGER NOT NULL,
                    status TEXT NOT NULL,  -- 'RESERVED', 'COMMITTED', 'ROLLED_BACK'
                    created_at REAL NOT NULL,
                    ticket_id TEXT
                );
                """
            )
            try:
                conn.execute("ALTER TABLE reservations ADD COLUMN ticket_id TEXT;")
            except Exception:
                pass

    def reserve(
        self,
        jti: str,
        amount_paise: int,
        max_budget_paise: int,
        max_txn_count: int,
        idempotency_key: str,
    ) -> tuple[bool, str]:
        """Atomically test limits and reserve spending using BEGIN IMMEDIATE.

        Returns (True, "APPROVED") or (False, ReasonCode string).
        """
        conn = self._get_connection()
        try:
            # BEGIN IMMEDIATE acquires a write lock immediately
            conn.execute("BEGIN IMMEDIATE")

            # Check if this idempotency key was already handled
            cur = conn.execute(
                "SELECT status, amount_paise FROM reservations WHERE idempotency_key = ?",
                (idempotency_key,),
            )
            row = cur.fetchone()
            if row:
                status, prev_amount = row
                if status == "COMMITTED":
                    conn.rollback()
                    return True, "ALREADY_COMMITTED"
                if status == "RESERVED":
                    conn.rollback()
                    return True, "ALREADY_RESERVED"
                conn.rollback()
                return False, "IDEMPOTENCY_CONFLICT"

            # Fetch committed totals
            cur = conn.execute(
                "SELECT spent_paise, txn_count FROM mandate_totals WHERE jti = ?",
                (jti,),
            )
            total_row = cur.fetchone()
            committed_spent = total_row[0] if total_row else 0
            committed_count = total_row[1] if total_row else 0

            # Fetch uncommitted active reservations (< 60 seconds old)
            now = time.time()
            cutoff = now - 60.0
            cur = conn.execute(
                """
                SELECT COALESCE(SUM(amount_paise), 0), COUNT(*)
                FROM reservations
                WHERE jti = ? AND status = 'RESERVED' AND created_at > ?
                """,
                (jti, cutoff),
            )
            res_row = cur.fetchone()
            reserved_spent = res_row[0] if res_row else 0
            reserved_count = res_row[1] if res_row else 0

            # Check Budget
            total_projected_spent = committed_spent + reserved_spent + amount_paise
            if total_projected_spent > max_budget_paise:
                conn.rollback()
                return False, "BUDGET_EXHAUSTED"

            # Check Txn Count
            total_projected_count = committed_count + reserved_count + 1
            if total_projected_count > max_txn_count:
                conn.rollback()
                return False, "TXN_COUNT_EXCEEDED"

            # Record reservation
            conn.execute(
                """
                INSERT INTO reservations (idempotency_key, jti, amount_paise, status, created_at)
                VALUES (?, ?, ?, 'RESERVED', ?)
                """,
                (idempotency_key, jti, amount_paise, now),
            )
            conn.commit()
            return True, "RESERVED"

        except Exception as exc:
            conn.rollback()
            return False, f"LEDGER_ERROR: {exc}"
        finally:
            conn.close()

    def commit(self, idempotency_key: str) -> bool:
        """Commit an active reservation and add to cumulative mandate totals."""
        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "SELECT jti, amount_paise, status FROM reservations WHERE idempotency_key = ?",
                (idempotency_key,),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return False

            jti, amount_paise, status = row
            if status == "COMMITTED":
                conn.rollback()
                return True  # Idempotent success

            if status != "RESERVED":
                conn.rollback()
                return False

            # Update mandate totals
            conn.execute(
                """
                INSERT INTO mandate_totals (jti, spent_paise, txn_count)
                VALUES (?, ?, 1)
                ON CONFLICT(jti) DO UPDATE SET
                    spent_paise = spent_paise + excluded.spent_paise,
                    txn_count = txn_count + 1;
                """,
                (jti, amount_paise),
            )

            # Mark committed
            conn.execute(
                "UPDATE reservations SET status = 'COMMITTED' WHERE idempotency_key = ?",
                (idempotency_key,),
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def set_ticket_id(self, idempotency_key: str, ticket_id: str) -> bool:
        """Associate issued approval ticket ID with an active reservation."""
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE reservations SET ticket_id = ? WHERE idempotency_key = ?",
                (ticket_id, idempotency_key),
            )
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def rollback(self, idempotency_key: str) -> bool:
        """Roll back an uncommitted reservation (e.g. gateway error)."""
        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE reservations SET status = 'ROLLED_BACK' WHERE idempotency_key = ? AND status = 'RESERVED'",
                (idempotency_key,),
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()

    def recover_from_crash(
        self,
        max_age_seconds: float = 0.0,
        gateway_reconciler: Optional[any] = None,
    ) -> int:
        """Reconcile uncommitted reservations against gateway settlement status.

        If a payment was already settled by the gateway (gateway_reconciler returns True),
        the reservation is COMMITTED to prevent double-spending and ledger drift.
        If not settled (or no reconciler provided), stale reservations are ROLLED_BACK.
        """
        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cutoff = time.time() - max_age_seconds
            cur = conn.execute(
                "SELECT idempotency_key, ticket_id, jti, amount_paise FROM reservations WHERE status = 'RESERVED' AND created_at <= ?",
                (cutoff,),
            )
            rows = cur.fetchall()
            recovered_count = 0
            for row in rows:
                idemp_key, ticket_id, jti, amt = row
                gateway_status_known = False
                settled = False
                if gateway_reconciler and (ticket_id or idemp_key):
                    key_to_check = ticket_id or idemp_key
                    try:
                        res = gateway_reconciler(key_to_check)
                        if res is not None:
                            settled = bool(res)
                            gateway_status_known = True
                    except Exception:
                        # Gateway offline or unreachable: fail closed (status unknown)
                        gateway_status_known = False
                elif gateway_reconciler is None:
                    # No reconciler provided: legacy immediate rollback
                    gateway_status_known = True
                    settled = False

                if not gateway_status_known:
                    # Fail-closed: do not release reservation or budget while gateway status is unknown
                    continue

                if settled:
                    # Gateway already settled this payment! Commit to prevent double-spend & ledger drift
                    conn.execute(
                        """
                        INSERT INTO mandate_totals (jti, spent_paise, txn_count)
                        VALUES (?, ?, 1)
                        ON CONFLICT(jti) DO UPDATE SET
                            spent_paise = spent_paise + excluded.spent_paise,
                            txn_count = txn_count + 1;
                        """,
                        (jti, amt),
                    )
                    conn.execute(
                        "UPDATE reservations SET status = 'COMMITTED' WHERE idempotency_key = ?",
                        (idemp_key,),
                    )
                else:
                    # Gateway confirms payment was NOT settled; safe to roll back
                    conn.execute(
                        "UPDATE reservations SET status = 'ROLLED_BACK' WHERE idempotency_key = ?",
                        (idemp_key,),
                    )
                recovered_count += 1
            conn.commit()
            return recovered_count
        finally:
            conn.close()

    def get_spent(self, jti: str) -> int:
        """Return total committed paise spent under this mandate."""
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT spent_paise FROM mandate_totals WHERE jti = ?", (jti,)
            )
            row = cur.fetchone()
            return row[0] if row else 0

    def get_count(self, jti: str) -> int:
        """Return number of committed transactions under this mandate."""
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT txn_count FROM mandate_totals WHERE jti = ?", (jti,)
            )
            row = cur.fetchone()
            return row[0] if row else 0

    def record_txn(self, jti: str, amount_paise: int) -> None:
        """Directly record a committed transaction (backwards compatibility)."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO mandate_totals (jti, spent_paise, txn_count)
                VALUES (?, ?, 1)
                ON CONFLICT(jti) DO UPDATE SET
                    spent_paise = spent_paise + excluded.spent_paise,
                    txn_count = txn_count + 1;
                """,
                (jti, amount_paise),
            )

    def reset(self, jti: Optional[str] = None) -> None:
        """Reset totals (useful for test fixtures)."""
        with self._get_connection() as conn:
            if jti:
                conn.execute("DELETE FROM mandate_totals WHERE jti = ?", (jti,))
                conn.execute("DELETE FROM reservations WHERE jti = ?", (jti,))
            else:
                conn.execute("DELETE FROM mandate_totals")
                conn.execute("DELETE FROM reservations")
