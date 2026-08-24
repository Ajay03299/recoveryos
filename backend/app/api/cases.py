from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import RecoveryCase

router = APIRouter(prefix="/api/recovery", tags=["recovery"])


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    segment: str
    previous_purchases: int
    lifetime_value_paise: int
    engagement_score: int


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    customer_id: str
    risk_type: str
    state: str
    amount_paise: int
    description: str
    payment_method: str
    failure_reason: str
    attempt_count: int
    days_overdue: int
    recovery_score: int | None
    diagnosis: str | None
    selected_strategy: str | None
    recovered_paise: int
    contacts_sent: int
    customer: CustomerOut


@router.get("/cases", response_model=list[CaseOut])
def list_cases(db: Session = Depends(get_db)):
    stmt = select(RecoveryCase).order_by(RecoveryCase.detected_at.desc())
    return db.scalars(stmt).all()


@router.get("/cases/{case_id}", response_model=CaseOut)
def get_case(case_id: str, db: Session = Depends(get_db)):
    case = db.get(RecoveryCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Recovery case not found")
    return case