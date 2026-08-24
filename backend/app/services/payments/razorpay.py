"""Razorpay test-mode provider (Payment Links API over httpx).

Credentials stay server-side. Webhook payloads are never trusted on their own:
we verify the signature, then re-query the API before recording any recovery.
"""
import hashlib
import hmac

import httpx

from app.core.config import settings
from app.services.payments import (
    PaymentError, PaymentLink, PaymentProvider, PaymentProviderUnavailable,
    PaymentState, PaymentStatus,
)

BASE_URL = "https://api.razorpay.com/v1"
TIMEOUT = 20.0

# Razorpay payment-link statuses -> our internal vocabulary.
STATUS_MAP = {
    "created": PaymentStatus.CREATED,
    "partially_paid": PaymentStatus.CREATED,
    "paid": PaymentStatus.PAID,
    "expired": PaymentStatus.EXPIRED,
    "cancelled": PaymentStatus.FAILED,
}


class RazorpayProvider(PaymentProvider):
    name = "razorpay"
    demo_mode = False

    def __init__(self) -> None:
        if not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET):
            raise PaymentError("Razorpay credentials are not configured.")
        self.auth = (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)

    def create_payment_link(self, *, case_id: str, amount_paise: int,
                            description: str, customer_name: str,
                            customer_email: str, customer_phone: str) -> PaymentLink:
        body = {
            "amount": amount_paise,
            "currency": "INR",
            "description": description[:255],
            "reference_id": f"{case_id}-{amount_paise}",
            "customer": {
                "name": customer_name,
                "email": customer_email,
                "contact": customer_phone,
            },
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "notes": {"recovery_case_id": case_id},
        }
        try:
            response = httpx.post(f"{BASE_URL}/payment_links", json=body,
                                  auth=self.auth, timeout=TIMEOUT)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            # 4xx is our bug; 5xx is theirs. Only the latter is safely retryable.
            if exc.response.status_code >= 500:
                raise PaymentProviderUnavailable(
                    f"Razorpay returned {exc.response.status_code}.") from exc
            raise PaymentError(
                f"Razorpay rejected the request: {exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise PaymentProviderUnavailable(f"Razorpay unreachable: {exc}") from exc

        return PaymentLink(
            reference=data["id"],
            url=data["short_url"],
            amount_paise=amount_paise,
            provider=self.name,
            demo_mode=False,
        )

    def get_payment_state(self, reference: str) -> PaymentState:
        try:
            response = httpx.get(f"{BASE_URL}/payment_links/{reference}",
                                 auth=self.auth, timeout=TIMEOUT)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise PaymentProviderUnavailable(
                f"Could not verify payment {reference}: {exc}") from exc

        return PaymentState(
            reference=reference,
            status=STATUS_MAP.get(data.get("status", ""), PaymentStatus.CREATED),
            amount_paid_paise=int(data.get("amount_paid") or 0),
            provider=self.name,
            demo_mode=False,
        )

    def verify_webhook(self, body: bytes, signature: str) -> bool:
        secret = settings.RAZORPAY_WEBHOOK_SECRET
        if not (secret and signature):
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
