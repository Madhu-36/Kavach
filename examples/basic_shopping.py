"""Basic Shopping Example using the @kavach.guard decorator.

Demonstrates how any Python payment function can be secured in 3 lines of code.
"""

from __future__ import annotations

import os
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from kavach.guard import guard
from kavach.issuer import generate_keypair, sign_mandate
from kavach.models import MandateSpec


# 1. Protect payment logic with @guard decorator
@guard(
    interceptor_url="http://127.0.0.1:8100",
    gateway_url="http://127.0.0.1:8000",
    payer_vpa="user@upi",
    category="groceries",
)
def buy_groceries(amount_paise: int, payee_vpa: str, note: str = ""):
    # This sensitive function body executes ONLY if Kavach approves!
    print(f"[GATEWAY] Moving ₹{amount_paise/100:.2f} to {payee_vpa}...")
    return {"status": "SUCCESS", "amount": amount_paise}


def main():
    print("=== Kavach Guard Quickstart ===")

    # Attempt 1: Legitimate purchase within mandate
    print("\nAttempting legitimate grocery purchase (₹450 to ondc-grocer@upi)...")
    res1 = buy_groceries(amount_paise=45_000, payee_vpa="ondc-grocer@upi", note="Basmati Rice")
    print(f"Result: {res1}")

    # Attempt 2: Attacker prompt injection attempt
    print("\nAttempting malicious payment (₹5,000 to attacker_wallet@upi)...")
    res2 = buy_groceries(amount_paise=500_000, payee_vpa="attacker_wallet@upi", note="Adversarial Drain")
    print(f"Result: {res2}")


if __name__ == "__main__":
    main()
