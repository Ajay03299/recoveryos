"""Payment provider abstraction.

Two modes, one interface. Simulated mode is always clearly labelled as such —
we never present a simulated payment as a real one.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from app.core.config import settings


class PaymentError(Exception):
    """Any payment operation that did not complete."""


class PaymentProviderUnavailable(PaymentError):
    """The provider could not be reached. Safe to retry — nothing was charged."""


class PaymentStatus(StrEnum):
    CREATED = "CREATED"
    PAID = "PAID"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


@dataclass
class PaymentLink:
    reference: str
    url: str
    amount_paise: int
    provider: str
    demo_mode: bool


@dataclass
class PaymentState:
    reference: str
    status: PaymentStatus
    amount_paid_paise: int
    provider: str
    demo_mode: bool


class PaymentProvider(ABC):
    name: str = "base"
    demo_mode: bool = False

    @abstractmethod
    def create_payment_link(self, *, case_id: str, amount_paise: int,
                            description: str, customer_name: str,
                            customer_email: str, customer_phone: str) -> PaymentLink: ...

    @abstractmethod
    def get_payment_state(self, reference: str) -> PaymentState: ...

    def verify_webhook(self, body: bytes, signature: str) -> bool:
        return False


def get_payment_provider(db: Session, name: str | None = None) -> PaymentProvider:
    provider = (name or settings.PAYMENT_PROVIDER).lower()

    if provider == "razorpay" and settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
        from app.services.payments.razorpay import RazorpayProvider
        return RazorpayProvider()

    from app.services.payments.simulated import SimulatedProvider
    return SimulatedProvider(db)
