"""LangChain Agent Example with Kavach-Guarded Payment Tool.

Demonstrates wrapping a LangChain StructuredTool with @kavach.guard.
The agent interacts with an ONDC product search tool and a payment tool.
Zero keys or mandate tokens are exposed to the LLM or tool schema.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from kavach.guard import guard


# Guarded Payment Tool for LangChain
@guard(
    interceptor_url="http://127.0.0.1:8100",
    gateway_url="http://127.0.0.1:8000",
    payer_vpa="user@upi",
    category="groceries",
)
def make_upi_payment(amount_paise: int, payee_vpa: str, note: str = "") -> dict:
    """Make a UPI payment to a merchant.
    
    Args:
        amount_paise: Amount to pay in integer paise (₹1 = 100 paise).
        payee_vpa: UPI ID of the recipient.
        note: Purpose or order reference.
    """
    return {"status": "SUCCESS", "message": f"Paid {amount_paise} paise to {payee_vpa}"}


def search_ondc_store(query: str) -> str:
    """Search for items in the ONDC grocery store."""
    return (
        "1. Organic Basmati Rice 5kg: ₹450 | Seller: ondc-grocer@upi\n"
        "2. Cold-Pressed Mustard Oil 1L: ₹220 | Seller: ondc-grocer@upi\n"
    )


def main():
    print("=== Kavach + LangChain Integration ===")
    print("Guarded tool 'make_upi_payment' registered.")
    print("Tool schema exposed to LLM:")
    import inspect
    sig = inspect.signature(make_upi_payment)
    print(f"make_upi_payment{sig}")
    print("\nNotice: Zero mandate tokens, keys, or internal rules appear in tool parameters!")


if __name__ == "__main__":
    main()
