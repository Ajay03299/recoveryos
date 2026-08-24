"""Deterministic offline payment provider.

Every artefact it produces is flagged demo_mode=True and surfaced as DEMO MODE
in the UI. No simulated payment is ever presented as a real transaction.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.flags import PAYMENT_PROVIDER_OUTAGE, get_flag
from app.models import PaymentRecord
from app.services.payments import (
    PaymentError, PaymentLink, PaymentProvider, PaymentProviderUnavailable,
    PaymentState, PaymentStatus,
)


class SimulatedProvider(PaymentProvider):
    name = "simulated"
    demo_mode = True

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_payment_link(self, *, case_id: str, amount_paise: int,
                            description: str, customer_name: str,
                            customer_email: str, customer_phone: str) -> PaymentLink:
        if get_flag(self.db, PAYMENT_PROVIDER_OUTAGE):
            raise PaymentProviderUnavailable(
                "Simulated payment provider outage is enabled.")

        reference = f"SIMPAY_{uuid.uuid4().hex[:12].upper()}"
        return PaymentLink(
            reference=reference,
            url=f"{settings.PUBLIC_WEB_URL}/pay/{reference}",
            amount_paise=amount_paise,
            provider=self.name,
            demo_mode=True,
        )

    def get_payment_state(self, reference: str) -> PaymentState:
        record = self.db.get(PaymentRecord, reference)
        if record is None:
            raise PaymentError(f"Unknown payment reference {reference}.")
        return PaymentState(
            reference=reference,
            status=PaymentStatus(record.status),
            amount_paid_paise=record.amount_paid_paise,
            provider=self.name,
            demo_mode=True,
        )

    def capture(self, reference: str) -> PaymentState:
        """Demo-only. Stands in for the customer completing checkout."""
        record = self.db.get(PaymentRecord, reference)
        if record is None:
            raise PaymentError(f"Unknown payment reference {reference}.")
        record.status = PaymentStatus.PAID
        record.amount_paid_paise = record.amount_paise
        record.paid_at = datetime.now(timezone.utc)
        self.db.commit()
        return self.get_payment_state(reference)
