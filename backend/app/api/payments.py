from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.flags import PAYMENT_PROVIDER_OUTAGE, get_flag, set_flag
from app.db.session import get_db
from app.models import PaymentRecord
from app.recovery.executor import confirm_payment
from app.services.payments import PaymentError, get_payment_provider
from app.services.payments.simulated import SimulatedProvider

router = APIRouter(prefix="/api/payments", tags=["payments"])


class PaymentOut(BaseModel):
    reference: str
    case_id: str
    provider: str
    demo_mode: bool
    amount_paise: int
    amount_paid_paise: int
    status: str
    url: str


class ConfirmOut(BaseModel):
    case_id: str
    state: str
    verified: bool
    status: str
    recovered_paise: int
    already_recorded: bool


@router.get("/{reference}", response_model=PaymentOut)
def get_payment(reference: str, db: Session = Depends(get_db)):
    record = db.get(PaymentRecord, reference)
    if record is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return PaymentOut(**{c.name: getattr(record, c.name)
                         for c in PaymentRecord.__table__.columns
                         if c.name in PaymentOut.model_fields})


@router.post("/{reference}/simulate-capture", response_model=ConfirmOut)
def simulate_capture(reference: str, db: Session = Depends(get_db)):
    """DEMO MODE ONLY. Stands in for the customer completing checkout."""
    provider = get_payment_provider(db)
    if not isinstance(provider, SimulatedProvider):
        raise HTTPException(
            status_code=403,
            detail="Simulated capture is disabled when a real payment provider "
                   "is configured.")
    try:
        provider.capture(reference)
        result = confirm_payment(db, reference, provider)
    except PaymentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return ConfirmOut(case_id=result.case.id, state=result.case.state,
                      verified=result.verified, status=result.status,
                      recovered_paise=result.recovered_paise,
                      already_recorded=result.already_recorded)


@router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db),
                  x_razorpay_signature: str = Header(default="")):
    """Razorpay webhook.

    The payload is only a hint. We verify the signature, then independently
    re-query the API before recording any recovery.
    """
    body = await request.body()
    provider = get_payment_provider(db)

    if not provider.verify_webhook(body, x_razorpay_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()
    entity = (payload.get("payload", {}).get("payment_link", {}).get("entity", {}))
    reference = entity.get("id")
    if not reference:
        return {"received": True, "action": "ignored"}

    try:
        result = confirm_payment(db, reference, provider)
    except PaymentError:
        return {"received": True, "action": "unknown_reference"}

    return {"received": True, "verified": result.verified,
            "case_id": result.case.id, "state": result.case.state}


class OutageIn(BaseModel):
    enabled: bool


@router.post("/demo/provider-outage")
def set_provider_outage(payload: OutageIn, db: Session = Depends(get_db)):
    """Toggle a simulated provider outage to demonstrate failure handling."""
    set_flag(db, PAYMENT_PROVIDER_OUTAGE, payload.enabled)
    return {"payment_provider_outage": get_flag(db, PAYMENT_PROVIDER_OUTAGE),
            "provider": settings.PAYMENT_PROVIDER}
