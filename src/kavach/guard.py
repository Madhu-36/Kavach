"""Kavach Guard Decorator — protect any payment tool in 3 lines of code.

Usage:
```python
from kavach.guard import guard

@guard(interceptor_url="http://127.0.0.1:8100", category="groceries")
def execute_payment(amount_paise: int, payee_vpa: str, note: str = ""):
    # This code executes ONLY if Kavach interceptor cryptographically approves!
    return upi_gateway.transfer(amount_paise, payee_vpa)
```
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, Optional

from kavach.client import KavachClient
from kavach.models import Decision, PaymentRequest, PaymentResult, ReasonCode


def guard(
    interceptor_url: str = "http://127.0.0.1:8100",
    gateway_url: str = "http://127.0.0.1:8000",
    payer_vpa: str = "user@upi",
    category: str = "groceries",
    auto_forward_to_gateway: bool = True,
) -> Callable:
    """Decorator protecting Python functions and LangChain tools with Kavach micro-mandates."""

    def decorator(fn: Callable) -> Callable:
        client = KavachClient(
            interceptor_url=interceptor_url,
            gateway_url=gateway_url,
            payer_vpa=payer_vpa,
        )

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Bind arguments to extract amount_paise and payee_vpa
            sig = inspect.signature(fn)
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            call_args = bound.arguments

            # Extract amount (supporting amount_paise or amount_inr)
            amount_paise: Optional[int] = None
            if "amount_paise" in call_args:
                amount_paise = int(call_args["amount_paise"])
            elif "amount_inr" in call_args:
                amount_paise = int(round(float(call_args["amount_inr"]) * 100))
            elif "amount" in call_args:
                val = call_args["amount"]
                amount_paise = int(val) if isinstance(val, int) else int(round(float(val) * 100))

            payee_vpa = str(call_args.get("payee_vpa") or call_args.get("payee") or "")
            note = str(call_args.get("note") or "")
            txn_category = str(call_args.get("category") or category)

            if amount_paise is None or not payee_vpa:
                raise ValueError("Guarded payment function must provide amount and payee_vpa")

            # Evaluate with Kavach Interceptor
            result: PaymentResult = client.request_payment(
                amount_paise=amount_paise,
                payee_vpa=payee_vpa,
                note=note,
                category=txn_category,
            )

            if not result.success:
                # Security Block: underlying function is NEVER invoked
                return {
                    "status": "BLOCKED",
                    "allowed": False,
                    "message": result.message,
                }

            # If auto-forwarded and succeeded, return gateway result or invoke fn
            if auto_forward_to_gateway and result.txn_id:
                return {
                    "status": "SUCCESS",
                    "allowed": True,
                    "txn_id": result.txn_id,
                    "message": result.message,
                }

            return fn(*args, **kwargs)

        return wrapper

    return decorator
